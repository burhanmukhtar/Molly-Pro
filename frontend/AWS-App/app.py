from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from bson.objectid import ObjectId
import os
import boto3
import uuid
import datetime
import jwt
import secrets
import urllib.parse
import certifi
import requests
import time
import logging
import threading
from botocore.exceptions import ClientError
from botocore.config import Config

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

# Setup AWS connection
AWS_ACCESS_KEY = ""
AWS_SECRET_KEY = ""

# Define AWS regions to use
AWS_REGIONS = [
    "us-east-1", "us-east-2"]

# AMI IDs for each region - these should be populated with actual AMI IDs for each region
REGION_AMI_MAP = {
    "us-east-1": "ami-0717fd362d93b348c",
    "us-east-2": "ami-03db07721ce0c5e15",  # Replace with actual AMI ID  # Replace with actual AMI ID
}

# Security group IDs for each region
REGION_SG_MAP = {
    "us-east-1": "sg-0b46f81635bd1b06e",
    "us-east-2": "sg-0123de08754ef4654",  # Replace with actual SG ID
}

# Key names for each region - assuming you're using the same key name in all regions
KEY_NAME = "tftf"

# Create a dictionary to store EC2 clients for each region
ec2_clients = {}
for region in AWS_REGIONS:
    ec2_clients[region] = boto3.client(
        'ec2',
        region_name=region,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
        config=Config(
            retries={
                'max_attempts': 10,
                'mode': 'adaptive'
            }
        )
    )

# Create MongoDB collection to track used Elastic IPs
# We'll use this to ensure we never reuse an IP
if 'UsedElasticIPs' not in db.list_collection_names():
    db.create_collection('UsedElasticIPs')
    # Create an index on IP address to make lookups faster
    db.UsedElasticIPs.create_index([('ip', 1)], unique=True)

# Function to find the optimal region for a new server
def find_optimal_region():
    """
    Find a region with fewer than 2 active users.
    Never use a full region (with 2 or more users).
    """
    # Count active users in each region
    region_user_counts = {region: [] for region in AWS_REGIONS}
    
    # Find all active servers
    current_time = datetime.datetime.utcnow()
    active_servers = db.Servers.find({
        'expiresAt': {'$gt': current_time}
    })
    
    # Count unique users per region
    for server in active_servers:
        if 'region' in server:
            region = server['region']
            user_id = server['user_id']
            
            if region in region_user_counts and user_id not in region_user_counts[region]:
                region_user_counts[region].append(user_id)
    
    # Find regions with fewer than 2 users
    available_regions = []
    for region, users in region_user_counts.items():
        if len(users) < 3:
            available_regions.append((region, len(users)))
    
    if not available_regions:
        logger.error("No regions available with fewer than 2 users!")
        raise Exception("All regions are at capacity (2 users per region limit reached)")
        
    # Sort by number of users (ascending)
    available_regions.sort(key=lambda x: x[1])
    
    # Select the region with the fewest users
    optimal_region = available_regions[0][0]
    logger.info(f"Selected optimal region: {optimal_region} with {available_regions[0][1]} active users")
    
    return optimal_region

# Helper function to check if EC2 instance is ready and the mailer service is running
def check_ec2_mailer_service(ip_address, max_attempts=30, retry_delay=10):
    """
    Check if the EC2 instance's mailer service is running
    
    Args:
        ip_address (str): EC2 instance public IP address
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

# Helper function for faster service checks with configurable timeouts
def check_ec2_mailer_service_with_timeout(ip_address, max_attempts=15, retry_delay=5):
    """
    Check if the EC2 instance's mailer service is running with configurable timeouts
    
    Args:
        ip_address (str): EC2 instance public IP address
        max_attempts (int): Maximum number of retry attempts
        retry_delay (int): Delay between retry attempts in seconds
        
    Returns:
        bool: True if service is running, False otherwise
    """
    for attempt in range(max_attempts):
        try:
            # Use a shorter timeout for faster checking
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

# Function to run service checks in background
def run_service_check_in_background(public_ip, server_id, max_attempts=15, retry_delay=5):
    """
    Run service check in a background thread and update server status once complete
    """
    def background_task():
        try:
            # Check if the instance's service is running
            service_check = check_ec2_mailer_service_with_timeout(
                public_ip, 
                max_attempts=max_attempts, 
                retry_delay=retry_delay
            )
            
            logger.info(f"Background mailer service check for {public_ip}: {'Success' if service_check else 'Failed'}")
            
            # Update server status in database
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {
                    'status': 'ready' if service_check else 'service_unavailable',
                    'updatedAt': datetime.datetime.utcnow()
                }}
            )
        except Exception as e:
            logger.error(f"Error in background service check: {str(e)}")
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {
                    'status': 'error',
                    'updatedAt': datetime.datetime.utcnow()
                }}
            )
    
    # Start the background thread
    thread = threading.Thread(target=background_task)
    thread.daemon = True
    thread.start()

# Function to run IP rotation background tasks
def run_ip_rotation_in_background(instance_id, server_id, current_public_ip, new_ip, region):
    """
    Complete IP rotation tasks in a background thread
    """
    def background_task():
        try:
            # Get the EC2 client for this region
            ec2 = ec2_clients.get(region)
            
            if not ec2:
                logger.error(f"No EC2 client found for region {region}")
                return
                
            # Look for and release the old elastic IP
            try:
                # If we have the current IP, try to find and release its allocation
                if current_public_ip:
                    old_addresses = ec2.describe_addresses(
                        Filters=[
                            {
                                'Name': 'public-ip', 
                                'Values': [current_public_ip]
                            }
                        ]
                    )
                    
                    if old_addresses.get('Addresses'):
                        for address in old_addresses['Addresses']:
                            allocation_id = address.get('AllocationId')
                            if allocation_id:
                                logger.info(f"Releasing old Elastic IP {current_public_ip} (AllocationId: {allocation_id}) in region {region}")
                                ec2.release_address(AllocationId=allocation_id)
            except Exception as release_error:
                logger.error(f"Error releasing old Elastic IP in region {region}: {str(release_error)}")
            
            # Check if mailer service is running on the new IP
            service_check = check_ec2_mailer_service_with_timeout(new_ip, max_attempts=30, retry_delay=10)
            logger.info(f"Background mailer service check for new IP {new_ip}: {'Success' if service_check else 'Failed'}")
            
            # Update server status in database
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {
                    'status': 'ready' if service_check else 'service_unavailable',
                    'updatedAt': datetime.datetime.utcnow()
                }}
            )
        except Exception as e:
            logger.error(f"Error in background IP rotation task: {str(e)}")
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {
                    'status': 'error',
                    'updatedAt': datetime.datetime.utcnow()
                }}
            )
    
    # Start the background thread
    thread = threading.Thread(target=background_task)
    thread.daemon = True
    thread.start()

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
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
    }, app.config['SECRET_KEY'], algorithm="HS256")
    
    # Convert ObjectId to string for JSON serialization
    user_data = {
        'id': str(user['_id']),
        'username': user['username'],
        'mollyPoints': user['mollyPoints']
    }
    
    return jsonify({'token': token, 'user': user_data})

@app.route('/api/user', methods=['GET'])
@token_required
def get_user(current_user):
    try:
        # Convert ObjectId to string for JSON serialization
        user_data = {
            'id': str(current_user['_id']),
            'username': current_user['username'],
            'mollyPoints': current_user['mollyPoints']
        }
        
        return jsonify({'user': user_data})
    except Exception as e:
        logger.error(f"Error getting user data: {str(e)}")
        return jsonify({'message': f'Failed to retrieve user data: {str(e)}'}), 500

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
        'expiresAt': {'$gt': datetime.datetime.utcnow()}
    }))
    
    if existing_servers:
        return jsonify({
            'message': 'You already have an active server. Please terminate it before creating a new one.',
            'has_existing_server': True,
            'server_id': str(existing_servers[0]['_id'])
        }), 409
    
    # Find the optimal region with the fewest servers
    selected_region = find_optimal_region()
    ami_id = REGION_AMI_MAP.get(selected_region)
    security_group_id = REGION_SG_MAP.get(selected_region)
    
    # Get the EC2 client for the selected region
    ec2 = ec2_clients[selected_region]
    
    # Create EC2 instance
    try:
        logger.info(f"Creating EC2 instance in region {selected_region} for user {current_user['username']}")
        
        # Create the EC2 instance
        response = ec2.run_instances(
            ImageId=ami_id,
            InstanceType='t2.medium',
            MinCount=1,
            MaxCount=1,
            SecurityGroupIds=[security_group_id],
            KeyName=KEY_NAME,
            UserData="""#!/bin/bash
/home/ubuntu/backend/
nohup node /home/ubuntu/backend/server.js > node-server.log 2>&1 &
""",
            TagSpecifications=[
                {
                    'ResourceType': 'instance',
                    'Tags': [
                        {
                            'Key': 'Name',
                            'Value': f"MollyServer-{current_user['username']}-{uuid.uuid4()}"
                        },
                    ]
                },
            ]
        )
        
        instance_id = response['Instances'][0]['InstanceId']
        
        logger.info(f"EC2 instance {instance_id} created in region {selected_region}, waiting for it to start running")
        
        # Wait for the instance to be running
        try:
            ec2.get_waiter('instance_running').wait(
                InstanceIds=[instance_id],
                WaiterConfig={
                    'Delay': 5,       # 5 seconds between each attempt
                    'MaxAttempts': 60 # Maximum 5 minutes of waiting (60 * 5 = 300 seconds)
                }
            )
            
            # Get instance details
            instance_info = ec2.describe_instances(InstanceIds=[instance_id])
            
            # For unlimited servers, allocate an Elastic IP
            if server_type == 'unlimited':
                # Allocate a new Elastic IP, ensuring it hasn't been used before
                max_retries = 5
                retry_count = 0
                new_ip_allocated = False
                new_allocation_id = None
                new_public_ip = None
                
                while retry_count < max_retries and not new_ip_allocated:
                    try:
                        # Allocate a new Elastic IP
                        logger.info(f"Allocating Elastic IP in region {selected_region} for unlimited server instance {instance_id}")
                        allocation_response = ec2.allocate_address(Domain='vpc')
                        allocation_id = allocation_response['AllocationId']
                        public_ip = allocation_response['PublicIp']
                        
                        # Check if this IP has been used before
                        if db.UsedElasticIPs.find_one({'ip': public_ip}):
                            logger.warning(f"IP {public_ip} has been used before, releasing and trying again")
                            ec2.release_address(AllocationId=allocation_id)
                            retry_count += 1
                        else:
                            # Record this IP as used
                            db.UsedElasticIPs.insert_one({
                                'ip': public_ip,
                                'region': selected_region,
                                'allocationId': allocation_id,
                                'createdAt': datetime.datetime.utcnow()
                            })
                            
                            # Associate the Elastic IP with the instance
                            logger.info(f"Associating Elastic IP {public_ip} with instance {instance_id} in region {selected_region}")
                            association_response = ec2.associate_address(
                                AllocationId=allocation_id,
                                InstanceId=instance_id
                            )
                            
                            # Tag the Elastic IP for easier management
                            ec2.create_tags(
                                Resources=[allocation_id],
                                Tags=[
                                    {
                                        'Key': 'Name',
                                        'Value': f"MollyServer-{current_user['username']}-{uuid.uuid4()}"
                                    },
                                    {
                                        'Key': 'ServerType',
                                        'Value': 'unlimited'
                                    }
                                ]
                            )
                            
                            new_allocation_id = allocation_id
                            new_public_ip = public_ip
                            new_ip_allocated = True
                    except Exception as e:
                        logger.error(f"Error allocating IP in region {selected_region}: {str(e)}")
                        retry_count += 1
                
                # If we couldn't allocate a fresh IP after max_retries, fail
                if not new_ip_allocated:
                    raise Exception(f"Failed to allocate a fresh IP after {max_retries} attempts")
                
                # Use the successfully allocated IP
                allocation_id = new_allocation_id
                public_ip = new_public_ip
            else:
                # For single servers, use the default public IP assigned by AWS
                public_ip = instance_info['Reservations'][0]['Instances'][0].get('PublicIpAddress')
                
                if not public_ip:
                    # Wait a bit longer and try again if the IP is not assigned yet
                    time.sleep(5)
                    instance_info = ec2.describe_instances(InstanceIds=[instance_id])
                    public_ip = instance_info['Reservations'][0]['Instances'][0].get('PublicIpAddress')
                    
                    if not public_ip:
                        raise Exception("Failed to get public IP address for the instance")
            
            logger.info(f"EC2 instance {instance_id} is running in region {selected_region} with IP {public_ip}")
            
            # Calculate expiry time based on server type
            if server_type == 'single':
                expires_in = datetime.timedelta(hours=2)
                cost = 100
            else:  # unlimited
                expires_in = datetime.timedelta(hours=12)
                cost = 2000
            
            expires_at = datetime.datetime.utcnow() + expires_in
            
            # Save server details to database with status tracking
            server = {
                'user_id': current_user['_id'],
                'instanceId': instance_id,
                'ip': public_ip,
                'type': server_type,
                'region': selected_region,  # Store the region for future reference
                'createdAt': datetime.datetime.utcnow(),
                'expiresAt': expires_at,
                'status': 'starting',  # Add status field for tracking
                'updatedAt': datetime.datetime.utcnow()  # Add timestamp for status tracking
            }
            
            result = db.Servers.insert_one(server)
            server_id = result.inserted_id
            
            # Start a background task to check if service is running
            run_service_check_in_background(public_ip, server_id)
            
            # Deduct Molly points from user
            db.Users.update_one(
                {'_id': current_user['_id']},
                {'$inc': {'mollyPoints': -cost}}
            )
            
            # Convert ObjectId to string for JSON serialization
            server['_id'] = str(server['_id'])
            server['user_id'] = str(server['user_id'])
            server['createdAt'] = server['createdAt'].isoformat()
            server['expiresAt'] = server['expiresAt'].isoformat()
            server['updatedAt'] = server['updatedAt'].isoformat()
            
            return jsonify({
                'message': 'Server creation initiated! Server will be ready shortly.',
                'server': server
            })
            
        except Exception as e:
            logger.error(f"Error creating EC2 instance in region {selected_region}: {str(e)}")
            
            # If instance was created but we encountered an error later, try to terminate it
            if 'instance_id' in locals():
                try:
                    logger.info(f"Attempting to terminate instance {instance_id} in region {selected_region} due to error")
                    ec2.terminate_instances(InstanceIds=[instance_id])
                except Exception as terminate_error:
                    logger.error(f"Error terminating EC2 instance in region {selected_region} after failure: {str(terminate_error)}")
            
            return jsonify({'message': f'Failed to create server: {str(e)}'}), 500
    
    except Exception as e:
        logger.error(f"Error in create_server: {str(e)}")
        return jsonify({'message': f'Failed to create server: {str(e)}'}), 500

@app.route('/api/servers', methods=['GET'])
@token_required
def get_servers(current_user):
    try:
        # Find all servers for this user, newest first
        servers = list(db.Servers.find({'user_id': current_user['_id']}).sort('createdAt', -1))
        
        # Check if each server is still valid (not expired)
        current_time = datetime.datetime.utcnow()
        valid_servers = []
        
        for server in servers:
            # Get the region for this server
            region = server.get('region', 'us-east-1')  # Default to us-east-1 for backward compatibility
            
            # Get the EC2 client for this region
            ec2 = ec2_clients.get(region)
            
            if not ec2:
                logger.error(f"No EC2 client found for region {region}")
                continue
                
            # Automatically remove expired servers
            if server['expiresAt'] < current_time:
                try:
                    # Try to terminate the EC2 instance silently
                    ec2.terminate_instances(InstanceIds=[server['instanceId']])
                    db.Servers.delete_one({'_id': server['_id']})
                    logger.info(f"Auto-terminated expired server: {server['instanceId']} in region {region}")
                    continue  # Skip this server
                except Exception as e:
                    logger.error(f"Error auto-terminating expired server {server['instanceId']} in region {region}: {str(e)}")
            
            # Convert ObjectId to string for JSON serialization
            server['_id'] = str(server['_id'])
            server['user_id'] = str(server['user_id'])
            server['createdAt'] = server['createdAt'].isoformat()
            server['expiresAt'] = server['expiresAt'].isoformat()
            if 'updatedAt' in server:
                server['updatedAt'] = server['updatedAt'].isoformat()
            
            # Check if the server's EC2 instance is still running
            try:
                instance_info = ec2.describe_instances(InstanceIds=[server['instanceId']])
                instance_state = instance_info['Reservations'][0]['Instances'][0]['State']['Name']
                server['state'] = instance_state
                
                if instance_state == 'running':
                    valid_servers.append(server)
                elif instance_state in ['terminated', 'shutting-down', 'stopped']:
                    # Remove servers that are no longer running
                    db.Servers.delete_one({'_id': ObjectId(server['_id'])})
                    logger.info(f"Removed non-running server {server['instanceId']} in region {region} in state {instance_state}")
                    continue
                else:
                    valid_servers.append(server)  # Include pending servers
            except Exception as e:
                logger.error(f"Error checking server state for {server['instanceId']} in region {region}: {str(e)}")
                server['state'] = 'unknown'
                valid_servers.append(server)
        
        return jsonify({'servers': valid_servers})
    except Exception as e:
        logger.error(f"Error getting servers: {str(e)}")
        return jsonify({'message': f'Failed to retrieve servers: {str(e)}', 'servers': []}), 500

@app.route('/api/server-status/<server_id>', methods=['GET'])
@token_required
def check_server_status(current_user, server_id):
    try:
        server = db.Servers.find_one({'_id': ObjectId(server_id), 'user_id': current_user['_id']})
        
        if not server:
            return jsonify({'message': 'Server not found!'}), 404
        
        # Get the region for this server
        region = server.get('region', 'us-east-1')  # Default to us-east-1 for backward compatibility
        
        # Get the EC2 client for this region
        ec2 = ec2_clients.get(region)
        
        if not ec2:
            logger.error(f"No EC2 client found for region {region}")
            return jsonify({'message': f'Server region {region} not supported!'}), 400
        
        # Check instance state directly in AWS for more accurate status
        instance_status = "unknown"
        try:
            instance_info = ec2.describe_instances(InstanceIds=[server['instanceId']])
            if instance_info['Reservations'] and instance_info['Reservations'][0]['Instances']:
                instance_status = instance_info['Reservations'][0]['Instances'][0]['State']['Name']
        except Exception as e:
            logger.warning(f"Failed to get instance status from AWS in region {region}: {str(e)}")
        
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
            'aws_status': instance_status,
            'region': region
        }
        
        if 'updatedAt' in server:
            server_data['updatedAt'] = server['updatedAt'].isoformat()
        
        # Check if we need to update status - support for status tracking
        # First, check if the server has a status field, if not, add it
        if 'status' not in server:
            # Set initial status based on instance status
            initial_status = 'ready' if instance_status == 'running' else 'starting'
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {'status': initial_status}}
            )
            server_data['status'] = initial_status
        
        # If status is still 'starting' or 'rotating_ip' after a long time, try to ping the service directly
        status = server.get('status', 'unknown')
        last_updated = server.get('updatedAt', server['createdAt'])
            
        current_time = datetime.datetime.utcnow()
        
        # Check if service is ready if status is in transitional state
        if status in ['starting', 'rotating_ip'] and instance_status == 'running':
            # Only check if the instance has been in this state for a while (60+ seconds)
            if not last_updated or (current_time - last_updated).total_seconds() > 60:
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
                
                # Set status to rechecking
                server_data['status'] = 'rechecking'
        
        # If instance is not running, update the status accordingly
        elif instance_status != 'running' and status == 'ready':
            new_status = 'starting' if instance_status in ['pending'] else 'stopped'
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {'status': new_status}}
            )
            server_data['status'] = new_status
            
        return jsonify({'server': server_data})
    
    except Exception as e:
        logger.error(f"Error checking server status: {str(e)}")
        return jsonify({'message': f'Failed to check server status: {str(e)}'}), 500

@app.route('/api/terminate-server/<server_id>', methods=['DELETE'])
@token_required
def terminate_server(current_user, server_id):
    try:
        server = db.Servers.find_one({'_id': ObjectId(server_id), 'user_id': current_user['_id']})
        
        if not server:
            return jsonify({'message': 'Server not found!'}), 404
        
        # Get the region for this server
        region = server.get('region', 'us-east-1')  # Default to us-east-1 for backward compatibility
        
        # Get the EC2 client for this region
        ec2 = ec2_clients.get(region)
        
        if not ec2:
            logger.error(f"No EC2 client found for region {region}")
            return jsonify({'message': f'Server region {region} not supported!'}), 400
            
        try:
            # Check instance state before attempting to terminate
            instance_info = ec2.describe_instances(InstanceIds=[server['instanceId']])
            instance_state = instance_info['Reservations'][0]['Instances'][0]['State']['Name']
            
            # Check if this is an unlimited server with an Elastic IP
            if server.get('type') == 'unlimited':
                try:
                    # Look for existing elastic IP associations for this instance
                    elastic_ips = ec2.describe_addresses(
                        Filters=[
                            {
                                'Name': 'instance-id', 
                                'Values': [server['instanceId']]
                            }
                        ]
                    )
                    
                    # Release any associated Elastic IPs
                    if elastic_ips.get('Addresses'):
                        for address in elastic_ips['Addresses']:
                            allocation_id = address.get('AllocationId')
                            if allocation_id:
                                logger.info(f"Releasing Elastic IP {address.get('PublicIp')} (AllocationId: {allocation_id}) in region {region}")
                                ec2.release_address(AllocationId=allocation_id)
                except Exception as elastic_ip_error:
                    logger.error(f"Error releasing Elastic IP in region {region}: {str(elastic_ip_error)}")
                    # Continue anyway to make sure we terminate the instance
            
            # Only terminate if the instance is not already terminated
            if instance_state not in ['terminated', 'shutting-down']:
                # Terminate the EC2 instance
                ec2.terminate_instances(InstanceIds=[server['instanceId']])
                logger.info(f"EC2 instance {server['instanceId']} in region {region} terminated by user {current_user['username']}")
            else:
                logger.info(f"EC2 instance {server['instanceId']} in region {region} already in state {instance_state}")
        except Exception as instance_error:
            logger.error(f"Error checking/terminating EC2 instance in region {region}: {str(instance_error)}")
            # Continue to remove the server from the database even if EC2 API call fails
        
        # Delete server record from database
        db.Servers.delete_one({'_id': ObjectId(server_id)})
        
        return jsonify({'message': 'Server terminated successfully!'})
    
    except Exception as e:
        logger.error(f"Error in terminate_server: {str(e)}")
        return jsonify({'message': f'Failed to terminate server: {str(e)}'}), 500

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
            
        # Get the region for this server
        region = server.get('region', 'us-east-1')  # Default to us-east-1 for backward compatibility
        
        # Get the EC2 client for this region
        ec2 = ec2_clients.get(region)
        
        if not ec2:
            logger.error(f"No EC2 client found for region {region}")
            return jsonify({'message': f'Server region {region} not supported!'}), 400
        
        # Update server status to rotating_ip
        db.Servers.update_one(
            {'_id': ObjectId(server_id)},
            {'$set': {
                'status': 'rotating_ip',
                'updatedAt': datetime.datetime.utcnow()
            }}
        )
        
        # First check if the instance exists and is running
        try:
            instance_info = ec2.describe_instances(InstanceIds=[instance_id])
            instance_state = instance_info['Reservations'][0]['Instances'][0]['State']['Name']
            
            if instance_state != 'running':
                db.Servers.update_one(
                    {'_id': ObjectId(server_id)},
                    {'$set': {
                        'status': 'error',
                        'updatedAt': datetime.datetime.utcnow()
                    }}
                )
                return jsonify({'message': f'Server is not in running state (current state: {instance_state})'}), 400
                
            # Get the current association if there is one
            current_public_ip = instance_info['Reservations'][0]['Instances'][0].get('PublicIpAddress')
            
            # Check if the current IP is an Elastic IP
            current_elastic_ip = None
            if current_public_ip:
                # Look for existing elastic IP associations for this instance
                elastic_ips = ec2.describe_addresses(
                    Filters=[
                        {
                            'Name': 'instance-id', 
                            'Values': [instance_id]
                        }
                    ]
                )
                
                if elastic_ips.get('Addresses'):
                    current_elastic_ip = elastic_ips['Addresses'][0].get('AllocationId')
            
            # Allocate a new Elastic IP
            logger.info(f"Allocating new Elastic IP in region {region} for instance {instance_id}")
            allocation_response = ec2.allocate_address(Domain='vpc')
            new_allocation_id = allocation_response['AllocationId']
            new_public_ip = allocation_response['PublicIp']
            
            # Associate the new Elastic IP with the instance
            logger.info(f"Associating new Elastic IP {new_public_ip} with instance {instance_id} in region {region}")
            association_response = ec2.associate_address(
                AllocationId=new_allocation_id,
                InstanceId=instance_id
            )
            
            # If we had a previous Elastic IP, release it
            if current_elastic_ip:
                try:
                    # Allow a short delay to ensure the new association is established
                    time.sleep(2)
                    logger.info(f"Releasing previous Elastic IP {current_public_ip} in region {region}")
                    ec2.release_address(AllocationId=current_elastic_ip)
                except Exception as release_error:
                    logger.error(f"Error releasing previous Elastic IP in region {region}: {str(release_error)}")
                    # Continue anyway since we've already associated the new IP
            
            # Update the server record with the new IP and status
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {
                    'ip': new_public_ip,
                    'status': 'rotating_ip',
                    'updatedAt': datetime.datetime.utcnow()
                }}
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
                'region': updated_server['region'],
                'createdAt': updated_server['createdAt'].isoformat(),
                'expiresAt': updated_server['expiresAt'].isoformat(),
                'status': updated_server['status'],
                'updatedAt': updated_server['updatedAt'].isoformat()
            }
            
            # Start background check for service readiness
            run_ip_rotation_in_background(instance_id, server_id, current_public_ip, new_public_ip, region)
            
            return jsonify({
                'message': 'IP rotation initiated! New IP will be ready shortly.',
                'server': server_data
            })
            
        except ClientError as client_error:
            logger.error(f"AWS client error in region {region}: {str(client_error)}")
            # Update status to error
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {
                    'status': 'error',
                    'updatedAt': datetime.datetime.utcnow()
                }}
            )
            return jsonify({'message': f'AWS client error: {str(client_error)}'}), 500
        except Exception as e:
            logger.error(f"Error rotating server IP in region {region}: {str(e)}")
            # Update status to error
            db.Servers.update_one(
                {'_id': ObjectId(server_id)},
                {'$set': {
                    'status': 'error',
                    'updatedAt': datetime.datetime.utcnow()
                }}
            )
            return jsonify({'message': f'Error rotating server IP: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"Error in rotate_ip: {str(e)}")
        return jsonify({'message': f'Failed to rotate server IP: {str(e)}'}), 500

# Healthcheck endpoint for the server
@app.route('/api/healthcheck', methods=['GET'])
def healthcheck():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.datetime.utcnow().isoformat(),
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
        'createdAt': datetime.datetime.utcnow()
    }
    
    result = db.Users.insert_one(new_user)
    
    return jsonify({
        'message': 'User created successfully!',
        'user_id': str(result.inserted_id)
    })

# Admin route to view all used Elastic IPs
@app.route('/api/admin/elastic-ips', methods=['GET'])
def get_elastic_ips():
    admin_key = request.headers.get('X-Admin-Key')
    if not admin_key or admin_key != os.environ.get('ADMIN_API_KEY', 'admin_secret_key'):
        return jsonify({'message': 'Unauthorized!'}), 401
        
    try:
        # Get all used Elastic IPs
        used_ips = list(db.UsedElasticIPs.find({}).sort('createdAt', -1))
        
        # Format for JSON response
        ip_data = []
        for ip in used_ips:
            ip_data.append({
                'ip': ip['ip'],
                'region': ip['region'],
                'allocationId': ip['allocationId'],
                'createdAt': ip['createdAt'].isoformat(),
                'releasedAt': ip.get('releasedAt', 'Not Released').isoformat() if isinstance(ip.get('releasedAt'), datetime.datetime) else 'Not Released'
            })
        
        # Get stats about IPs
        total_ips = len(ip_data)
        ips_by_region = {}
        for ip in ip_data:
            region = ip['region']
            if region not in ips_by_region:
                ips_by_region[region] = 0
            ips_by_region[region] += 1
        
        return jsonify({
            'total_used_ips': total_ips,
            'ips_by_region': ips_by_region,
            'used_ips': ip_data
        })
    except Exception as e:
        logger.error(f"Error getting Elastic IP data: {str(e)}")
        return jsonify({'message': f'Failed to get Elastic IP data: {str(e)}'}), 500

# Serve static files
@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

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
    return jsonify({'status': 'healthy', 'time': datetime.datetime.utcnow().isoformat()})

# Enhanced: Modified to handle multi-region servers
def terminate_expired_servers():
    try:
        current_time = datetime.datetime.utcnow()
        expired_servers = db.Servers.find({
            'expiresAt': {'$lt': current_time}
        })
        
        count = 0
        for server in expired_servers:
            try:
                # Get the region for this server
                region = server.get('region', 'us-east-1')  # Default to us-east-1 for backward compatibility
                
                # Get the EC2 client for this region
                ec2 = ec2_clients.get(region)
                
                if not ec2:
                    logger.error(f"No EC2 client found for region {region}")
                    continue
                    
                # Check if this is an unlimited server with an Elastic IP
                if server.get('type') == 'unlimited':
                    try:
                        # Look for existing elastic IP associations for this instance
                        elastic_ips = ec2.describe_addresses(
                            Filters=[
                                {
                                    'Name': 'instance-id', 
                                    'Values': [server['instanceId']]
                                }
                            ]
                        )
                        
                        # Release any associated Elastic IPs
                        if elastic_ips.get('Addresses'):
                            for address in elastic_ips['Addresses']:
                                allocation_id = address.get('AllocationId')
                                public_ip = address.get('PublicIp')
                                if allocation_id:
                                    logger.info(f"Releasing Elastic IP {public_ip} (AllocationId: {allocation_id}) from expired server in region {region}")
                                    ec2.release_address(AllocationId=allocation_id)
                                    
                                    # Mark this IP as used in our database even when releasing
                                    if not db.UsedElasticIPs.find_one({'ip': public_ip}):
                                        db.UsedElasticIPs.insert_one({
                                            'ip': public_ip,
                                            'region': region,
                                            'allocationId': allocation_id,
                                            'createdAt': datetime.datetime.utcnow(),
                                            'releasedAt': datetime.datetime.utcnow()
                                        })
                    except Exception as elastic_ip_error:
                        logger.error(f"Error releasing Elastic IP for expired server in region {region}: {str(elastic_ip_error)}")
                        # Continue anyway to make sure we terminate the instance
                
                # Check instance state before attempting to terminate
                try:
                    instance_info = ec2.describe_instances(InstanceIds=[server['instanceId']])
                    instance_state = instance_info['Reservations'][0]['Instances'][0]['State']['Name']
                    
                    # Only terminate if the instance is not already terminated
                    if instance_state not in ['terminated', 'shutting-down']:
                        # Terminate the EC2 instance
                        ec2.terminate_instances(InstanceIds=[server['instanceId']])
                    else:
                        logger.info(f"Expired server {server['instanceId']} in region {region} already in state {instance_state}")
                except Exception as instance_error:
                    logger.error(f"Error checking/terminating expired server EC2 instance in region {region}: {str(instance_error)}")
                
                # Delete server record from database regardless of EC2 termination result
                db.Servers.delete_one({'_id': server['_id']})
                count += 1
                
                logger.info(f"Terminated expired server: {server['instanceId']} in region {region}")
            except Exception as e:
                logger.error(f"Error handling expired server {server['instanceId']}: {str(e)}")
        
        if count > 0:
            logger.info(f"Terminated {count} expired servers")
            
    except Exception as e:
        logger.error(f"Error in terminate_expired_servers: {str(e)}")

if __name__ == '__main__':
    # Run scheduled task once at startup
    terminate_expired_servers()
    
    # Start the Flask app
    app.run(debug=False, host='0.0.0.0', port=80)