from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from bson.objectid import ObjectId
import os
import uuid
import datetime
import jwt
import secrets
import urllib.parse
import certifi
import requests
import time
import logging
from google.cloud import compute_v1
from google.auth import exceptions
from google.oauth2 import service_account
import threading
from flask import jsonify, render_template, send_from_directory

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_secret_key')

# MongoDB Configuration with explicit certifi certificate
username = urllib.parse.quote_plus("bazhar691")
password = urllib.parse.quote_plus("Naruto@007")

# Set longer timeouts and more retries
mongo_uri = f"mongodb+srv://{username}:{password}@voidmailer.4ivgowc.mongodb.net/VoidMailer?retryWrites=true&w=majority&connectTimeoutMS=30000&socketTimeoutMS=30000&serverSelectionTimeoutMS=30000&maxIdleTimeMS=45000"

# Setup MongoDB connection with explicit certifi certificate path
client = MongoClient(
    mongo_uri,
    tlsCAFile=certifi.where(),
    connect=False,  # Connect lazily
    connectTimeoutMS=30000,
    socketTimeoutMS=30000,
    serverSelectionTimeoutMS=30000,
    maxIdleTimeMS=45000
)
db = client.VoidMailer

# Configure app logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Setup Google Cloud authentication - assumes a service account key file is available
# You should set this environment variable to point to your service account key file
GOOGLE_APPLICATION_CREDENTIALS = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', './molly-pro-452801-5272e06a4346.json')
gcp_project_id = os.environ.get('GCP_PROJECT_ID', 'server-config-d')
gcp_zone = os.environ.get('GCP_ZONE', 'us-central1-c')  # Default zone
gcp_region = os.environ.get('GCP_REGION', 'us-central1')  # Default region


# Initialize Google Cloud client for Compute Engine
try:
    # Try to use service account credentials if available
    if os.path.exists(GOOGLE_APPLICATION_CREDENTIALS):
        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_APPLICATION_CREDENTIALS
        )
        
        # Create Google Cloud Compute Engine clients
        instance_client = compute_v1.InstancesClient(credentials=credentials)
        addresses_client = compute_v1.AddressesClient(credentials=credentials)
        global_addresses_client = compute_v1.GlobalAddressesClient(credentials=credentials)
        instance_groups_client = compute_v1.InstanceGroupsClient(credentials=credentials)
        operation_client = compute_v1.ZoneOperationsClient(credentials=credentials)
        region_operation_client = compute_v1.RegionOperationsClient(credentials=credentials)
        
        # Create firewall client and global operation client
        firewall_client = compute_v1.FirewallsClient(credentials=credentials)
        global_operation_client = compute_v1.GlobalOperationsClient(credentials=credentials)
    else:
        # Use application default credentials if no service account key file
        instance_client = compute_v1.InstancesClient()
        addresses_client = compute_v1.AddressesClient()
        global_addresses_client = compute_v1.GlobalAddressesClient()
        instance_groups_client = compute_v1.InstanceGroupsClient()
        operation_client = compute_v1.ZoneOperationsClient()
        region_operation_client = compute_v1.RegionOperationsClient()
        firewall_client = compute_v1.FirewallsClient()
        global_operation_client = compute_v1.GlobalOperationsClient()
        
    logger.info("Successfully initialized Google Cloud clients")
except exceptions.DefaultCredentialsError as e:
    logger.error(f"Failed to authenticate with Google Cloud: {str(e)}")
    raise

# Middleware for JWT token verification
def token_required(f):
    def decorated(*args, **kwargs):
        token = None
        
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
        
        # For testing: Also check for token in query parameters or form data
        if not token and 'token' in request.args:
            token = request.args.get('token')
        
        if not token and request.form and 'token' in request.form:
            token = request.form.get('token')
            
        # For testing: Also check for token in JSON body
        if not token and request.is_json:
            json_data = request.get_json()
            if json_data and 'token' in json_data:
                token = json_data.get('token')
        
        if not token:
            return jsonify({'message': 'Token is missing!'}), 401
        
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            current_user = db.Users.find_one({'_id': ObjectId(data['user_id'])})
            if not current_user:
                return jsonify({'message': 'User not found!'}), 401
        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'Token has expired!'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'message': 'Invalid token!'}), 401
        except Exception as e:
            logger.error(f"Token decode error: {str(e)}")
            return jsonify({'message': 'Token validation error!'}), 401
        
        return f(current_user, *args, **kwargs)
    
    decorated.__name__ = f.__name__
    return decorated

# Helper function to check if GCP instance is ready and the mailer service is running
def check_gcp_mailer_service(ip_address, max_attempts=30, retry_delay=10):
    """
    Check if the GCP instance's mailer service is running
    
    Args:
        ip_address (str): GCP instance public IP address
        max_attempts (int): Maximum number of retry attempts
        retry_delay (int): Delay between retry attempts in seconds
        
    Returns:
        bool: True if service is running, False otherwise
    """
    for attempt in range(max_attempts):
        try:
            # Attempt to connect to the mailer service
            response = requests.get(f"http://{ip_address}:5000/", timeout=5)
            
            if response.status_code == 200:
                logger.info(f"Mailer service is running at {ip_address} after {attempt + 1} attempts")
                return True
        except requests.RequestException as e:
            logger.info(f"Attempt {attempt + 1}/{max_attempts} to connect to mailer service failed: {str(e)}")
        
        # Sleep before next attempt
        if attempt < max_attempts - 1:
            time.sleep(retry_delay)
    
    logger.error(f"Failed to connect to mailer service at {ip_address} after {max_attempts} attempts")
    return False

# Helper functions for different GCP operation types
# Helper function to wait for a GCP zonal operation to complete
def wait_for_operation(operation, zone=gcp_zone):
    """
    Wait for a GCP zonal operation to complete
    
    Args:
        operation: The operation to wait for
        zone: The zone where the operation is running
        
    Returns:
        The final operation status
    """
    logger.info(f"Waiting for zonal operation {operation.name} to complete")
    
    while True:
        # Get the operation status
        result = operation_client.get(
            project=gcp_project_id,
            zone=zone,
            operation=operation.name
        )
        
        if result.status == compute_v1.Operation.Status.DONE:
            if result.error:
                error_message = result.error.errors[0].message if result.error.errors else "Unknown error"
                logger.error(f"Zonal operation {operation.name} failed: {error_message}")
                raise Exception(f"Zonal operation failed: {error_message}")
            else:
                logger.info(f"Zonal operation {operation.name} completed successfully")
                return result
                
        # Wait before checking the status again
        time.sleep(1)

# Helper function to wait for a GCP regional operation to complete
def wait_for_regional_operation(operation, region=gcp_region):
    """
    Wait for a GCP regional operation to complete
    
    Args:
        operation: The operation to wait for
        region: The region where the operation is running
        
    Returns:
        The final operation status
    """
    logger.info(f"Waiting for regional operation {operation.name} to complete")
    
    while True:
        # Get the operation status
        result = region_operation_client.get(
            project=gcp_project_id,
            region=region,
            operation=operation.name
        )
        
        if result.status == compute_v1.Operation.Status.DONE:
            if result.error:
                error_message = result.error.errors[0].message if result.error.errors else "Unknown error"
                logger.error(f"Regional operation {operation.name} failed: {error_message}")
                raise Exception(f"Regional operation failed: {error_message}")
            else:
                logger.info(f"Regional operation {operation.name} completed successfully")
                return result
                
        # Wait before checking the status again
        time.sleep(1)

# Helper function to wait for a GCP global operation to complete
def wait_for_global_operation(operation):
    """
    Wait for a GCP global operation to complete
    
    Args:
        operation: The operation to wait for
        
    Returns:
        The final operation status
    """
    logger.info(f"Waiting for global operation {operation.name} to complete")
    
    while True:
        # Get the operation status
        result = global_operation_client.get(
            project=gcp_project_id,
            operation=operation.name
        )
        
        if result.status == compute_v1.Operation.Status.DONE:
            if result.error:
                error_message = result.error.errors[0].message if result.error.errors else "Unknown error"
                logger.error(f"Global operation {operation.name} failed: {error_message}")
                raise Exception(f"Global operation failed: {error_message}")
            else:
                logger.info(f"Global operation {operation.name} completed successfully")
                return result
                
        # Wait before checking the status again
        time.sleep(1)

# Routes
@app.route('/')
def index():
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/login', methods=['POST'])
def login():
    # Handle both JSON and form data
    if request.is_json:
        data = request.get_json()
    else:
        data = request.form
    
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({'message': 'Missing username or password!'}), 400
    
    user = db.Users.find_one({'username': data['username']})
    
    # Modified to use plaintext password comparison instead of check_password_hash
    if not user or user['password'] != data['password']:
        return jsonify({'message': 'Invalid username or password!'}), 401
    
    # Create JWT token
    token = jwt.encode({
        'user_id': str(user['_id']),
        'exp': datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=24)
    }, app.config['SECRET_KEY'], algorithm="HS256")
    
    # Convert ObjectId to string for JSON serialization
    user_data = {
        'id': str(user['_id']),
        'username': user['username'],
        'mollyPoints': user['mollyPoints']
    }
    
    return jsonify({'token': token, 'user': user_data})


@app.route('/api/servers', methods=['GET'])
@token_required
def get_servers(current_user):
    try:
        # Find all servers for this user, newest first
        servers = list(db.Servers.find({'user_id': current_user['_id']}).sort('createdAt', -1))
        
        # Check if each server is still valid (not expired)
        current_time = datetime.datetime.now(datetime.UTC)
        valid_servers = []
        
        for server in servers:
            # Convert expiration time to timezone-aware if it's naive
            expires_at = server['expiresAt']
            if expires_at.tzinfo is None:
                # Assume stored time is UTC if no timezone info
                expires_at = expires_at.replace(tzinfo=datetime.UTC)
            
            # Automatically remove expired servers
            if expires_at < current_time:
                try:
                    # Try to terminate the GCP instance silently
                    # Skip termination if instanceId is not in GCP format
                    if server['instanceId'].startswith('molly-server-'):
                        operation = instance_client.delete(
                            project=gcp_project_id,
                            zone=server.get('gcp_zone', gcp_zone),
                            instance=server['instanceId']
                        )
                        wait_for_operation(operation, zone=server.get('gcp_zone', gcp_zone))
                    else:
                        logger.info(f"Skipping termination of non-GCP instance ID: {server['instanceId']}")
                        
                    db.Servers.delete_one({'_id': server['_id']})
                    logger.info(f"Auto-terminated expired server: {server['instanceId']}")
                    continue  # Skip this server
                except Exception as e:
                    logger.error(f"Error auto-terminating expired server {server['instanceId']}: {str(e)}")
            
            # Convert ObjectId to string for JSON serialization
            server['_id'] = str(server['_id'])
            server['user_id'] = str(server['user_id'])
            server['createdAt'] = server['createdAt'].isoformat()
            server['expiresAt'] = server['expiresAt'].isoformat()
            
            # Check if the server's GCP instance is still running
            try:
                # Only check instance state for GCP-formatted instance IDs
                if server['instanceId'].startswith('molly-server-'):
                    instance_info = instance_client.get(
                        project=gcp_project_id,
                        zone=server.get('gcp_zone', gcp_zone),
                        instance=server['instanceId']
                    )
                    server['state'] = instance_info.status
                    
                    if instance_info.status == 'RUNNING':
                        valid_servers.append(server)
                    elif instance_info.status in ['TERMINATED', 'STOPPING', 'STOPPED']:
                        # Remove servers that are no longer running
                        db.Servers.delete_one({'_id': ObjectId(server['_id'])})
                        logger.info(f"Removed non-running server {server['instanceId']} in state {instance_info.status}")
                        continue
                    else:
                        valid_servers.append(server)  # Include pending servers
                else:
                    # For older/non-GCP instances, keep them but mark as legacy
                    server['state'] = 'legacy'
                    valid_servers.append(server)
            except Exception as e:
                logger.error(f"Error checking server state for {server['instanceId']}: {str(e)}")
                server['state'] = 'unknown'
                valid_servers.append(server)
        
        return jsonify({'servers': valid_servers})
    except Exception as e:
        logger.error(f"Error getting servers: {str(e)}")
        user_data = {
            'id': str(current_user['_id']),
            'username': current_user['username'],
            'mollyPoints': current_user['mollyPoints']
        }
        
        return jsonify({
            'servers': valid_servers,
            'user': user_data  # Add this line
        })
    except Exception as e:
        return jsonify({'message': f'Failed to retrieve servers: {str(e)}', 'servers': []}), 500

# Here's the complete fixed terminate_server function
@app.route('/api/terminate-server/<server_id>', methods=['DELETE'])
@token_required
def terminate_server(current_user, server_id):
    try:
        server = db.Servers.find_one({'_id': ObjectId(server_id), 'user_id': current_user['_id']})
        
        if not server:
            return jsonify({'message': 'Server not found!'}), 404
        
        try:
            # Only attempt to terminate if instanceId is in GCP format
            if server['instanceId'].startswith('molly-server-'):
                # Check instance state before attempting to terminate
                instance_info = instance_client.get(
                    project=gcp_project_id,
                    zone=server.get('gcp_zone', gcp_zone),
                    instance=server['instanceId']
                )
                
                # Check if this is an unlimited server with a static IP
                if server.get('type') == 'unlimited':
                    try:
                        # FIXED: Don't use filter parameter, manually filter instead
                        static_ips = []
                        for address in addresses_client.list(
                            project=gcp_project_id,
                            region=server.get('gcp_region', gcp_region)
                        ):
                            # Filter the addresses manually
                            if address.address == server["ip"]:
                                static_ips.append(address)
                        
                        # Release any associated static IPs
                        for address in static_ips:
                            logger.info(f"Releasing static IP {address.address} (Name: {address.name})")
                            operation = addresses_client.delete(
                                project=gcp_project_id,
                                region=server.get('gcp_region', gcp_region),
                                address=address.name
                            )
                            wait_for_regional_operation(operation, region=server.get('gcp_region', gcp_region))
                    except Exception as static_ip_error:
                        logger.error(f"Error releasing static IP: {str(static_ip_error)}")
                        # Continue anyway to make sure we terminate the instance
                
                # Only terminate if the instance is not already terminated
                if instance_info.status not in ['TERMINATED', 'STOPPING']:
                    # Terminate the GCP instance
                    operation = instance_client.delete(
                        project=gcp_project_id,
                        zone=server.get('gcp_zone', gcp_zone),
                        instance=server['instanceId']
                    )
                    wait_for_operation(operation, zone=server.get('gcp_zone', gcp_zone))
                    logger.info(f"GCP instance {server['instanceId']} terminated by user {current_user['username']}")
                else:
                    logger.info(f"GCP instance {server['instanceId']} already in state {instance_info.status}")
            else:
                # For legacy instance IDs, just log that we're skipping termination
                logger.info(f"Skipping termination of non-GCP instance ID: {server['instanceId']}")
        except Exception as instance_error:
            logger.error(f"Error checking/terminating GCP instance: {str(instance_error)}")
            # Continue to remove the server from the database even if GCP API call fails
        
        # Delete server record from database
        db.Servers.delete_one({'_id': ObjectId(server_id)})
        
        return jsonify({'message': 'Server terminated successfully!'})
    
    except Exception as e:
        logger.error(f"Error in terminate_server: {str(e)}")
        return jsonify({'message': f'Failed to terminate server: {str(e)}'}), 500

# Healthcheck endpoint for the server
@app.route('/api/healthcheck', methods=['GET'])
def healthcheck():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.datetime.now(datetime.UTC).isoformat(),
        'server': 'Molly Server API'
    })

# Admin route to create a new user
@app.route('/api/admin/create-user', methods=['POST'])
def create_user():
    # In a production environment, this would require admin authentication
    admin_key = request.headers.get('X-Admin-Key')
    if not admin_key or admin_key != os.environ.get('ADMIN_API_KEY', 'admin_secret_key'):
        return jsonify({'message': 'Unauthorized!'}), 401
    
    # Handle both JSON and form data
    if request.is_json:
        data = request.get_json()
    else:
        data = request.form
    
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({'message': 'Missing username or password!'}), 400
    
    # Check if username already exists
    if db.Users.find_one({'username': data['username']}):
        return jsonify({'message': 'Username already exists!'}), 400
    
    # Create new user with plaintext password instead of hashed password
    new_user = {
        'username': data['username'],
        'password': data['password'],  # Store password as plaintext
        'mollyPoints': int(data.get('mollyPoints', 0)),
        'createdAt': datetime.datetime.now(datetime.UTC)
    }
    
    result = db.Users.insert_one(new_user)
    
    return jsonify({
        'message': 'User created successfully!',
        'user_id': str(result.inserted_id)
    })

# Scheduled task to terminate expired servers (would be run by a cron job)
# Here's the complete fixed terminate_expired_servers function
def terminate_expired_servers():
    try:
        current_time = datetime.datetime.now(datetime.UTC)
        expired_servers = db.Servers.find({
            'expiresAt': {'$lt': current_time}
        })
        
        count = 0
        for server in expired_servers:
            try:
                # Only process GCP-formatted instance IDs
                if not server['instanceId'].startswith('molly-server-'):
                    logger.info(f"Skipping termination of non-GCP instance ID: {server['instanceId']}")
                    db.Servers.delete_one({'_id': server['_id']})
                    count += 1
                    continue
                
                # Check if this is an unlimited server with a static IP
                if server.get('type') == 'unlimited':
                    try:
                        # FIXED: Don't use filter parameter, manually filter instead
                        static_ips = []
                        for address in addresses_client.list(
                            project=gcp_project_id,
                            region=server.get('gcp_region', gcp_region)
                        ):
                            # Filter the addresses manually
                            if address.address == server["ip"]:
                                static_ips.append(address)
                        
                        # Release any associated static IPs
                        for address in static_ips:
                            logger.info(f"Releasing static IP {address.address} (Name: {address.name}) from expired server")
                            operation = addresses_client.delete(
                                project=gcp_project_id,
                                region=server.get('gcp_region', gcp_region),
                                address=address.name
                            )
                            wait_for_regional_operation(operation, region=server.get('gcp_region', gcp_region))
                    except Exception as static_ip_error:
                        logger.error(f"Error releasing static IP for expired server: {str(static_ip_error)}")
                
                # Check instance state before attempting to terminate
                try:
                    instance_info = instance_client.get(
                        project=gcp_project_id,
                        zone=server.get('gcp_zone', gcp_zone),
                        instance=server['instanceId']
                    )
                    
                    # Only terminate if the instance is not already terminated
                    if instance_info.status not in ['TERMINATED', 'STOPPING']:
                        # Terminate the GCP instance
                        operation = instance_client.delete(
                            project=gcp_project_id,
                            zone=server.get('gcp_zone', gcp_zone),
                            instance=server['instanceId']
                        )
                        wait_for_operation(operation, zone=server.get('gcp_zone', gcp_zone))
                    else:
                        logger.info(f"Expired server {server['instanceId']} already in state {instance_info.status}")
                except Exception as instance_error:
                    logger.error(f"Error checking/terminating expired server GCP instance: {str(instance_error)}")
                
                # Delete server record from database regardless of GCP termination result
                db.Servers.delete_one({'_id': server['_id']})
                count += 1
                
                logger.info(f"Terminated expired server: {server['instanceId']}")
            except Exception as e:
                logger.error(f"Error handling expired server {server['instanceId']}: {str(e)}")
        
        if count > 0:
            logger.info(f"Terminated {count} expired servers")
            
    except Exception as e:
        logger.error(f"Error in terminate_expired_servers: {str(e)}")

# Serve static files
@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

@app.route('/api/user/points', methods=['GET'])
@token_required
def get_user_points(current_user):
    try:
        # Get fresh user data from database
        user = db.Users.find_one({'_id': current_user['_id']})
        if not user:
            return jsonify({'message': 'User not found!'}), 404
            
        return jsonify({'mollyPoints': user['mollyPoints']})
    except Exception as e:
        logger.error(f"Error getting user points: {str(e)}")
        return jsonify({'message': f'Failed to get user points: {str(e)}'}), 500

# Serve the dashboard.html template
@app.route('/templates/<path:path>')
def serve_template(path):
    return send_from_directory('templates', path)

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'message': 'Endpoint not found'}), 404

@app.errorhandler(500)
def server_error(error):
    logger.error(f"Internal server error: {str(error)}")
    return jsonify({'message': 'Internal server error'}), 500

@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({'message': 'Method not allowed'}), 405

@app.errorhandler(400)
def bad_request(error):
    return jsonify({'message': 'Bad request'}), 400

# Health check endpoint
@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy', 'time': datetime.datetime.now(datetime.UTC).isoformat()})


# Add this new helper function above the route definitions
def run_service_check_in_background(public_ip, server_id):
    """
    Run service check in a background thread and update server status once complete
    """
    def background_task():
        try:
            # Wait for instance to fully initialize and service to start
            service_check = check_gcp_mailer_service(public_ip, max_attempts=30, retry_delay=10)
            logger.info(f"Background mailer service check for {public_ip}: {'Success' if service_check else 'Failed'}")
            
            # Update server status in database
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {'status': 'ready' if service_check else 'service_unavailable'}}
            )
        except Exception as e:
            logger.error(f"Error in background service check: {str(e)}")
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {'status': 'error'}}
            )
    
    # Start the background thread
    thread = threading.Thread(target=background_task)
    thread.daemon = True
    thread.start()

# Add this new helper function for IP rotation background task
def run_ip_rotation_in_background(instance_id, server_id, current_public_ip, new_ip, zone, region):
    """
    Complete IP rotation tasks in a background thread
    """
    def background_task():
        try:
            # Look for and release the old static IP
            try:
                old_static_ips = []
                for address in addresses_client.list(
                    project=gcp_project_id,
                    region=region
                ):
                    # Filter the addresses manually
                    if address.address == current_public_ip:
                        old_static_ips.append(address)
                
                for address in old_static_ips:
                    logger.info(f"Releasing old static IP {address.address} (Name: {address.name})")
                    operation = addresses_client.delete(
                        project=gcp_project_id,
                        region=region,
                        address=address.name
                    )
                    wait_for_regional_operation(operation, region=region)
            except Exception as release_error:
                logger.error(f"Error releasing old static IP: {str(release_error)}")
            
            # Check if mailer service is running on the new IP
            service_check = check_gcp_mailer_service(new_ip, max_attempts=30, retry_delay=10)
            logger.info(f"Background mailer service check for new IP {new_ip}: {'Success' if service_check else 'Failed'}")
            
            # Update server status in database
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {'status': 'ready' if service_check else 'service_unavailable'}}
            )
        except Exception as e:
            logger.error(f"Error in background IP rotation task: {str(e)}")
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {'status': 'error'}}
            )
    
    # Start the background thread
    thread = threading.Thread(target=background_task)
    thread.daemon = True
    thread.start()

# Now modify the create_server function to be asynchronous
@app.route('/api/create-server', methods=['POST'])
@token_required
def create_server(current_user):
    # Handle both JSON and form data
    if request.is_json:
        data = request.get_json()
    else:
        data = request.form
    
    server_type = data.get('type')
    
    if not server_type:
        return jsonify({'message': 'Server type is required!'}), 400
    
    # Check if user has enough Molly points
    if server_type == 'single' and current_user['mollyPoints'] < 100:
        return jsonify({'message': 'Not enough Molly points! You need 100 points for a single server.'}), 400
    
    if server_type == 'unlimited' and current_user['mollyPoints'] < 2000:
        return jsonify({'message': 'Not enough Molly points! You need 2000 points for unlimited servers.'}), 400
    
    # Check if user already has active servers
    existing_servers = list(db.Servers.find({
        'user_id': current_user['_id'],
        'expiresAt': {'$gt': datetime.datetime.now(datetime.UTC)}
    }))
    
    if existing_servers:
        return jsonify({
            'message': 'You already have an active server. Please terminate it before creating a new one.',
            'has_existing_server': True,
            'server_id': str(existing_servers[0]['_id'])
        }), 409
    
    # Create GCP Compute Engine instance
    try:
        # Use your specific custom image
        custom_image_name = os.environ.get('GCP_CUSTOM_IMAGE', 'molly-js')
        custom_image_project = os.environ.get('GCP_IMAGE_PROJECT', gcp_project_id)
        
        logger.info(f"Creating GCP instance for user {current_user['username']} using image {custom_image_name} from project {custom_image_project}")
        
        # Create a name for the VM
        vm_name = f"molly-server-{current_user['username']}-{uuid.uuid4().hex[:8]}".lower()
        
        # Create the disk configuration
        disk = compute_v1.AttachedDisk()
        disk.boot = True
        disk.auto_delete = True
        
        initialize_params = compute_v1.AttachedDiskInitializeParams()
        
        # Try different image sources - first try your project, then try "debian-cloud"
        try_image_sources = [
            # Try custom image in your project
            f"projects/{custom_image_project}/global/images/{custom_image_name}",
            # Fallback to debian-11 if custom image not found
            "projects/debian-cloud/global/images/family/debian-11"
        ]
        
        # Try to find a valid image
        valid_image = None
        for image_source in try_image_sources:
            try:
                # Just log which image we're trying
                logger.info(f"Trying image source: {image_source}")
                initialize_params.source_image = image_source
                valid_image = image_source
                break
            except Exception as e:
                logger.warning(f"Image source {image_source} not valid: {str(e)}")
        
        if not valid_image:
            raise Exception("No valid disk image found. Please check your image name and project.")
            
        initialize_params.source_image = valid_image
        initialize_params.disk_size_gb = 10  # 10 GB disk size
        initialize_params.disk_type = f"zones/{gcp_zone}/diskTypes/pd-ssd"
        
        disk.initialize_params = initialize_params
        
        # Create the network interface configuration
        network_interface = compute_v1.NetworkInterface()
        network_interface.name = "global/networks/default"
        
        # Allow external IP access
        access_config = compute_v1.AccessConfig()
        access_config.name = "External NAT"
        access_config.type_ = "ONE_TO_ONE_NAT"
        access_config.network_tier = "PREMIUM"
        
        network_interface.access_configs = [access_config]
        
        # Create startup script (equivalent to UserData in AWS)
        startup_script = """#!/bin/bash
cd /home/black19990521/molly-wkhmtltopdf
nohup node server.js > node-server.log 2>&1 &
python3 send.py
"""

        # Create the metadata items including the startup script
        metadata = compute_v1.Metadata()
        metadata_items = [
            compute_v1.Items(
                key="startup-script",
                value=startup_script
            )
        ]
        metadata.items = metadata_items
        
        # Create the instance configuration
        instance = compute_v1.Instance()
        instance.name = vm_name
        instance.machine_type = f"zones/{gcp_zone}/machineTypes/e2-standard-2"  # Equivalent to t2.medium
        instance.disks = [disk]
        instance.network_interfaces = [network_interface]
        instance.metadata = metadata
        
        # Set labels (equivalent to tags in AWS)
        instance.labels = {
            "user": current_user['username'].lower(),
            "servertype": server_type,
            "service": "mollyserver"
        }
        
        # Create the instance
        operation = instance_client.insert(
            project=gcp_project_id,
            zone=gcp_zone,
            instance_resource=instance
        )
        
        # Wait for the create operation to complete
        wait_for_operation(operation)
        
        # Get the created instance
        created_instance = instance_client.get(
            project=gcp_project_id,
            zone=gcp_zone,
            instance=vm_name
        )
        
        # Get the public IP address
        public_ip = None
        for interface in created_instance.network_interfaces:
            for access_config in interface.access_configs:
                if access_config.name == "External NAT":
                    public_ip = access_config.nat_i_p  # Changed from nat_ip to nat_i_p
                    break
        
        if not public_ip:
            raise Exception("Failed to get public IP address for the instance")
        
        logger.info(f"GCP instance {vm_name} is running with IP {public_ip}")
        
        # For unlimited servers, allocate a static IP
        if server_type == 'unlimited':
            # Create a name for the static IP
            static_ip_name = f"molly-ip-{current_user['username']}-{uuid.uuid4().hex[:8]}".lower()
            
            # Create a static IP address in the region
            address = compute_v1.Address()
            address.name = static_ip_name
            address.description = f"Static IP for {vm_name}"
            
            # Create the address
            operation = addresses_client.insert(
                project=gcp_project_id,
                region=gcp_region,
                address_resource=address
            )
            
            # Wait for the create operation to complete
            wait_for_regional_operation(operation)
            
            # Get the created address
            static_address = addresses_client.get(
                project=gcp_project_id,
                region=gcp_region,
                address=static_ip_name
            )
            
            # Update the instance to use the static IP
            # First, delete the existing access config
            operation = instance_client.delete_access_config(
                project=gcp_project_id,
                zone=gcp_zone,
                instance=vm_name,
                access_config=access_config.name,
                network_interface="nic0"
            )
            
            wait_for_operation(operation)
            
            # Then, add a new access config with the static IP
            access_config = compute_v1.AccessConfig()
            access_config.name = "External NAT"
            access_config.type_ = "ONE_TO_ONE_NAT"
            # Fix: Use the proper field for setting the IP address
            # Try different field names that might be correct for your version of the API
            try:
                # Most likely field name based on Python conventions
                access_config.nat_i_p = static_address.address  # Changed from nat_ip
            except Exception as e:
                logger.error(f"Failed to set nat_i_p: {str(e)}")
                # Try alternative field names
                try:
                    access_config.nat_ip_address = static_address.address
                except Exception:
                    # If all else fails, try setting it via __setattr__
                    setattr(access_config, 'natIp', static_address.address)
                
            access_config.network_tier = "PREMIUM"
            
            operation = instance_client.add_access_config(
                project=gcp_project_id,
                zone=gcp_zone,
                instance=vm_name,
                network_interface="nic0",
                access_config_resource=access_config
            )
            
            wait_for_operation(operation)
            
            # Update the public IP
            public_ip = static_address.address
        
        # Calculate expiry time based on server type
        if server_type == 'single':
            expires_in = datetime.timedelta(hours=2)
            cost = 100
        else:  # unlimited
            expires_in = datetime.timedelta(hours=12)
            cost = 2000
        
        expires_at = datetime.datetime.now(datetime.UTC) + expires_in
        
        # Save server details to database
        server = {
            'user_id': current_user['_id'],
            'instanceId': vm_name,
            'ip': public_ip,
            'type': server_type,
            'createdAt': datetime.datetime.now(datetime.UTC),
            'expiresAt': expires_at,
            'gcp_zone': gcp_zone,
            'gcp_region': gcp_region,
            'status': 'starting'  # New field to track server status
        }
        
        result = db.Servers.insert_one(server)
        server_id = result.inserted_id
        
        # Deduct Molly points from user
        db.Users.update_one(
            {'_id': current_user['_id']},
            {'$inc': {'mollyPoints': -cost}}
        )
        
        # Start service check in background
        run_service_check_in_background(public_ip, server_id)
        
        # Convert ObjectId to string for JSON serialization
        server['_id'] = str(server['_id'])
        server['user_id'] = str(server['user_id'])
        server['createdAt'] = server['createdAt'].isoformat()
        server['expiresAt'] = server['expiresAt'].isoformat()
        
        return jsonify({
            'message': 'Server creation initiated! Server will be ready shortly.',
            'server': server
        })
        
    except Exception as e:
        logger.error(f"Error creating GCP instance: {str(e)}")
        
        # If instance was created but we encountered an error later, try to terminate it
        if 'vm_name' in locals():
            try:
                logger.info(f"Attempting to terminate instance {vm_name} due to error")
                operation = instance_client.delete(
                    project=gcp_project_id,
                    zone=gcp_zone,
                    instance=vm_name
                )
                wait_for_operation(operation)
            except Exception as terminate_error:
                logger.error(f"Error terminating GCP instance after failure: {str(terminate_error)}")
        
        return jsonify({'message': f'Failed to create server: {str(e)}'}), 500

# Modify the rotate_ip function to be asynchronous
@app.route('/api/rotate-ip', methods=['POST'])
@token_required
def rotate_ip(current_user):
    try:
        # Handle both JSON and form data
        if request.is_json:
            data = request.get_json()
        else:
            data = request.form
        
        server_id = data.get('server_id')
        
        if not server_id:
            return jsonify({'message': 'Server ID is required!'}), 400
        
        # Find the current server
        server = db.Servers.find_one({'_id': ObjectId(server_id), 'user_id': current_user['_id']})
        
        if not server:
            return jsonify({'message': 'Server not found!'}), 404
        
        # Verify this is an unlimited server
        if server.get('type') != 'unlimited':
            return jsonify({'message': 'IP rotation is only available for unlimited servers!'}), 400
            
        # Get the instance ID from the server
        instance_id = server.get('instanceId')
        
        if not instance_id:
            return jsonify({'message': 'Invalid server data!'}), 400
        
        # First check if the instance exists and is running
        try:
            # Mark the server as updating
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {'status': 'rotating_ip'}}
            )
            
            instance_info = instance_client.get(
                project=gcp_project_id,
                zone=server.get('gcp_zone', gcp_zone),
                instance=instance_id
            )
            
            if instance_info.status != 'RUNNING':
                return jsonify({'message': f'Server is not in running state (current state: {instance_info.status})'}), 400
                
            # Get the current external IP address
            current_public_ip = None
            access_config_name = None
            for interface in instance_info.network_interfaces:
                for access_config in interface.access_configs:
                    current_public_ip = access_config.nat_i_p
                    access_config_name = access_config.name
                    break
            
            if not current_public_ip:
                return jsonify({'message': 'Server has no public IP address!'}), 400
            
            # Create a name for the new static IP
            new_static_ip_name = f"molly-ip-{current_user['username']}-{uuid.uuid4().hex[:8]}".lower()
            
            # Create a new static IP address in the region
            address = compute_v1.Address()
            address.name = new_static_ip_name
            address.description = f"Static IP for {instance_id}"
            
            # Create the address
            operation = addresses_client.insert(
                project=gcp_project_id,
                region=server.get('gcp_region', gcp_region),
                address_resource=address
            )
            
            # Wait for the create operation to complete
            wait_for_regional_operation(operation, region=server.get('gcp_region', gcp_region))
            
            # Get the created address
            static_address = addresses_client.get(
                project=gcp_project_id,
                region=server.get('gcp_region', gcp_region),
                address=new_static_ip_name
            )
            
            # Update the instance to use the new static IP
            # First, delete the existing access config
            operation = instance_client.delete_access_config(
                project=gcp_project_id,
                zone=server.get('gcp_zone', gcp_zone),
                instance=instance_id,
                access_config=access_config_name,
                network_interface="nic0"
            )
            
            wait_for_operation(operation, zone=server.get('gcp_zone', gcp_zone))
            
            # Then, add a new access config with the static IP
            access_config = compute_v1.AccessConfig()
            access_config.name = "External NAT"
            access_config.type_ = "ONE_TO_ONE_NAT"
            # Fix: Use the proper field for setting the IP address
            try:
                # Most likely field name based on Python conventions
                access_config.nat_i_p = static_address.address
            except Exception as e:
                logger.error(f"Failed to set nat_i_p: {str(e)}")
                # Try alternative field names
                try:
                    access_config.nat_ip_address = static_address.address
                except Exception:
                    # If all else fails, try setting it via __setattr__
                    setattr(access_config, 'natIp', static_address.address)
                
            access_config.network_tier = "PREMIUM"
            
            operation = instance_client.add_access_config(
                project=gcp_project_id,
                zone=server.get('gcp_zone', gcp_zone),
                instance=instance_id,
                network_interface="nic0",
                access_config_resource=access_config
            )
            
            wait_for_operation(operation, zone=server.get('gcp_zone', gcp_zone))
            
            # Update the server record with the new IP
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {'ip': static_address.address}}
            )
            
            # Get the updated server record
            updated_server = db.Servers.find_one({'_id': ObjectId(server_id)})
            
            # Prepare response data
            server_data = {
                '_id': str(updated_server['_id']),
                'user_id': str(updated_server['user_id']),
                'instanceId': updated_server['instanceId'],
                'ip': updated_server['ip'],
                'type': updated_server['type'],
                'createdAt': updated_server['createdAt'].isoformat(),
                'expiresAt': updated_server['expiresAt'].isoformat(),
                'status': 'rotating_ip'
            }
            
            # Start background task to clean up old IP and verify service
            run_ip_rotation_in_background(
                instance_id, 
                server_id, 
                current_public_ip, 
                static_address.address,
                server.get('gcp_zone', gcp_zone),
                server.get('gcp_region', gcp_region)
            )
            
            return jsonify({
                'message': 'IP rotation initiated! New IP will be ready shortly.',
                'server': server_data
            })
            
        except Exception as client_error:
            logger.error(f"Google Cloud client error: {str(client_error)}")
            return jsonify({'message': f'Google Cloud client error: {str(client_error)}'}), 500
    except Exception as e:
        logger.error(f"Error in rotate_ip: {str(e)}")
        return jsonify({'message': f'Failed to rotate server IP: {str(e)}'}), 500

# Add a new endpoint to check server status
# Optimize the check_server_status function to provide more detailed status information
@app.route('/api/server-status/<server_id>', methods=['GET'])
@token_required
def check_server_status(current_user, server_id):
    try:
        server = db.Servers.find_one({'_id': ObjectId(server_id), 'user_id': current_user['_id']})
        
        if not server:
            return jsonify({'message': 'Server not found!'}), 404
        
        # Check instance state directly in GCP for more accurate status
        instance_status = "unknown"
        try:
            instance_info = instance_client.get(
                project=gcp_project_id,
                zone=server.get('gcp_zone', gcp_zone),
                instance=server['instanceId']
            )
            instance_status = instance_info.status
        except Exception as e:
            logger.warning(f"Failed to get instance status from GCP: {str(e)}")
        
        # Create response data with enriched status information
        server_data = {
            '_id': str(server['_id']),
            'user_id': str(server['user_id']),
            'instanceId': server['instanceId'],
            'ip': server['ip'],
            'type': server['type'],
            'createdAt': server['createdAt'].isoformat(),
            'expiresAt': server['expiresAt'].isoformat(),
            'status': server.get('status', 'unknown'),
            'gcp_status': instance_status
        }
        
        # If status is still 'starting' or 'rotating_ip' after a long time, try to ping the service directly
        status = server.get('status', 'unknown')
        last_updated = server.get('updatedAt', server['createdAt'])
        
        # Ensure last_updated is timezone-aware
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=datetime.UTC)
            
        current_time = datetime.datetime.now(datetime.UTC)
        
        # Reduced wait time before rechecking from 120 to 60 seconds
        if (status in ['starting', 'rotating_ip'] and 
            (current_time - last_updated).total_seconds() > 60):
            
            # Check service directly with shorter timeout
            try:
                response = requests.get(f"http://{server['ip']}:5000/", timeout=3)
                if response.status_code == 200:
                    # Service is actually ready
                    db.Servers.update_one(
                        {'_id': ObjectId(server_id)},
                        {'$set': {'status': 'ready', 'updatedAt': current_time}}
                    )
                    server_data['status'] = 'ready'
                    return jsonify({'server': server_data})
            except requests.RequestException:
                # Service not ready yet, continue with recheck
                pass
                
            # Update the timestamp to prevent multiple rechecks
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {'updatedAt': current_time}}
            )
            
            # Start a new background service check with shorter timeouts
            run_service_check_in_background(server['ip'], server['_id'], max_attempts=15, retry_delay=5)
            server_data['status'] = 'rechecking'
            
        return jsonify({'server': server_data})
    
    except Exception as e:
        logger.error(f"Error checking server status: {str(e)}")
        return jsonify({'message': f'Failed to check server status: {str(e)}'}), 500

# Modified function to run service checks faster
def run_service_check_in_background(public_ip, server_id, max_attempts=15, retry_delay=5):
    """
    Run service check in a background thread and update server status once complete
    """
    def background_task():
        try:
            # Wait for instance to fully initialize and service to start
            service_check = check_gcp_mailer_service(public_ip, max_attempts=max_attempts, retry_delay=retry_delay)
            logger.info(f"Background mailer service check for {public_ip}: {'Success' if service_check else 'Failed'}")
            
            # Update server status in database
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {'status': 'ready' if service_check else 'service_unavailable'}}
            )
        except Exception as e:
            logger.error(f"Error in background service check: {str(e)}")
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {'status': 'error'}}
            )
    
    # Start the background thread
    thread = threading.Thread(target=background_task)
    thread.daemon = True
    thread.start()

# Modify the check_gcp_mailer_service function for faster checking
def check_gcp_mailer_service(ip_address, max_attempts=15, retry_delay=5):
    """
    Check if the GCP instance's mailer service is running with shorter timeouts
    
    Args:
        ip_address (str): GCP instance public IP address
        max_attempts (int): Maximum number of retry attempts
        retry_delay (int): Delay between retry attempts in seconds
        
    Returns:
        bool: True if service is running, False otherwise
    """
    for attempt in range(max_attempts):
        try:
            # Attempt to connect to the mailer service with shorter timeout
            response = requests.get(f"http://{ip_address}:5000/", timeout=3)
            
            if response.status_code == 200:
                logger.info(f"Mailer service is running at {ip_address} after {attempt + 1} attempts")
                return True
        except requests.RequestException as e:
            logger.info(f"Attempt {attempt + 1}/{max_attempts} to connect to mailer service failed: {str(e)}")
        
        # Sleep before next attempt
        if attempt < max_attempts - 1:
            time.sleep(retry_delay)
    
    logger.error(f"Failed to connect to mailer service at {ip_address} after {max_attempts} attempts")
    return False

# Function to ensure required firewall rules exist
def ensure_firewall_rules():
    """
    Check and create necessary firewall rules if they don't exist
    """
    try:
        # Define needed firewall rules
        required_rules = [
            {
                'name': 'allow-molly-http',
                'description': 'Allow HTTP traffic to Molly servers',
                'network': 'global/networks/default',
                'direction': 'INGRESS',
                'priority': 1000,
                'allowed': [{'IPProtocol': 'tcp', 'ports': ['80']}],
                'source_ranges': ['0.0.0.0/0']
            },
            {
                'name': 'allow-molly-https',
                'description': 'Allow HTTPS traffic to Molly servers',
                'network': 'global/networks/default',
                'direction': 'INGRESS',
                'priority': 1000,
                'allowed': [{'IPProtocol': 'tcp', 'ports': ['443']}],
                'source_ranges': ['0.0.0.0/0']
            },
            {
                'name': 'allow-molly-mailer',
                'description': 'Allow traffic to Molly mailer service',
                'network': 'global/networks/default',
                'direction': 'INGRESS',
                'priority': 1000,
                'allowed': [{'IPProtocol': 'tcp', 'ports': ['5000']}],
                'source_ranges': ['0.0.0.0/0']
            },
            {
                'name': 'allow-molly-ssh',
                'description': 'Allow SSH to Molly servers',
                'network': 'global/networks/default',
                'direction': 'INGRESS',
                'priority': 1000,
                'allowed': [{'IPProtocol': 'tcp', 'ports': ['22']}],
                'source_ranges': ['0.0.0.0/0']
            }
        ]
        
        # Get existing firewall rules
        existing_rules = {}
        request = firewall_client.list(project=gcp_project_id)
        for item in request:
            existing_rules[item.name] = item
            
        # Create any missing rules
        for rule_config in required_rules:
            if rule_config['name'] not in existing_rules:
                logger.info(f"Creating firewall rule: {rule_config['name']}")
                
                # Create the firewall rule
                firewall_rule = compute_v1.Firewall()
                firewall_rule.name = rule_config['name']
                firewall_rule.description = rule_config['description']
                firewall_rule.network = rule_config['network']
                firewall_rule.direction = rule_config['direction']
                firewall_rule.priority = rule_config['priority']
                
                # Set allowed protocols and ports
                allowed_list = []
                for allowed_item in rule_config['allowed']:
                    allowed = compute_v1.Allowed()
                    allowed.I_p_protocol = allowed_item['IPProtocol']
                    allowed.ports = allowed_item['ports']
                    allowed_list.append(allowed)
                firewall_rule.allowed = allowed_list
                
                # Set source ranges
                firewall_rule.source_ranges = rule_config['source_ranges']
                
                # Create the rule
                operation = firewall_client.insert(
                    project=gcp_project_id,
                    firewall_resource=firewall_rule
                )
                
                # Wait for the operation to complete - use global operations client
                wait_for_global_operation(operation)
                
                logger.info(f"Firewall rule {rule_config['name']} created successfully")
            else:
                logger.info(f"Firewall rule {rule_config['name']} already exists")
                
    except Exception as e:
        logger.error(f"Error ensuring firewall rules: {str(e)}")
        # Don't raise exception - application should still start even if firewall rules couldn't be created

if __name__ == '__main__':
    # Ensure required firewall rules exist
    ensure_firewall_rules()
    
    # Run scheduled task once at startup
    terminate_expired_servers()
    
    # Start the Flask app
    app.run(debug=False, host='0.0.0.0', port=80)
