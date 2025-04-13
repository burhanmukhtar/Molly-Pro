import os
import logging
import datetime
import uuid
import threading
import time
import requests
from flask import request, jsonify, render_template, current_app
from bson.objectid import ObjectId
from google.cloud import compute_v1

from auth import token_required, create_token
from gcp_utils import wait_for_operation, wait_for_regional_operation, check_gcp_mailer_service
from ip_tracking import get_fresh_static_ip

# Setup logging
logger = logging.getLogger(__name__)

def register_routes(app, db, gcp_clients, config):
    """
    Register all routes with the Flask app
    
    Args:
        app: Flask application
        db: MongoDB database
        gcp_clients: GCP client objects
        config: Application configuration
    """
    
    # Store db and config in app context for access in route handlers
    # This is essential for the auth system to work properly
    app.config['db'] = db
    app.config['gcp_clients'] = gcp_clients
    app.config['app_config'] = config
    
    # Log that the database is available
    logger.info(f"Database connection registered in app.config. Collections: {db.list_collection_names()}")
    
    ###########################################
    # Background Service Functions
    ###########################################
    
    def run_service_check_in_background(public_ip, server_id, max_attempts=30, retry_delay=10):
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
    
    def run_ip_rotation_in_background(instance_id, server_id, current_public_ip, new_ip, zone, region):
        """
        Complete IP rotation tasks in a background thread
        """
        def background_task():
            try:
                # Look for and release the old static IP
                try:
                    old_static_ips = []
                    for address in gcp_clients.addresses_client.list(
                        project=config.GCP_PROJECT_ID,
                        region=region
                    ):
                        # Filter the addresses manually
                        if address.address == current_public_ip:
                            old_static_ips.append(address)
                    
                    for address in old_static_ips:
                        logger.info(f"Releasing old static IP {address.address} (Name: {address.name})")
                        operation = gcp_clients.addresses_client.delete(
                            project=config.GCP_PROJECT_ID,
                            region=region,
                            address=address.name
                        )
                        wait_for_regional_operation(
                            gcp_clients.region_operation_client, 
                            operation, 
                            config.GCP_PROJECT_ID, 
                            region
                        )
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
    
    ###########################################
    # Page Routes
    ###########################################
    
    @app.route('/')
    def index():
        return render_template('login.html')

    @app.route('/dashboard')
    def dashboard():
        return render_template('dashboard.html')
    
    ###########################################
    # Authentication Routes
    ###########################################

    @app.route('/api/test-db')
    def test_db():
        try:
            # Check if db is in app.config
            if 'db' not in current_app.config:
                return jsonify({
                    'status': 'error',
                    'message': 'No db in app.config',
                    'config_keys': list(current_app.config.keys())
                })
                
            # Try to access the database
            collections = current_app.config['db'].list_collection_names()
            return jsonify({
                'status': 'success',
                'collections': collections
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': str(e)
            })
    
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
        token = create_token(user['_id'])
        
        # Convert ObjectId to string for JSON serialization
        user_data = {
            'id': str(user['_id']),
            'username': user['username'],
            'mollyPoints': user['mollyPoints']
        }
        
        return jsonify({'token': token, 'user': user_data})
    
    ###########################################
    # Admin Routes
    ###########################################
    
    @app.route('/api/admin/create-user', methods=['POST'])
    def create_user():
        # In a production environment, this would require admin authentication
        admin_key = request.headers.get('X-Admin-Key')
        if not admin_key or admin_key != config.ADMIN_API_KEY:
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
    
    ###########################################
    # Health Check Routes
    ###########################################
    
    @app.route('/api/healthcheck', methods=['GET'])
    def healthcheck():
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.datetime.now(datetime.UTC).isoformat(),
            'server': 'Molly Server API'
        })
    
    ###########################################
    # User Routes
    ###########################################
    
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
    
    
    ###########################################
    # Server Management Routes
    ###########################################
    
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
                            operation = gcp_clients.instance_client.delete(
                                project=config.GCP_PROJECT_ID,
                                zone=server.get('gcp_zone', config.GCP_ZONE),
                                instance=server['instanceId']
                            )
                            wait_for_operation(
                                gcp_clients.operation_client, 
                                operation, 
                                config.GCP_PROJECT_ID, 
                                server.get('gcp_zone', config.GCP_ZONE)
                            )
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
                        instance_info = gcp_clients.instance_client.get(
                            project=config.GCP_PROJECT_ID,
                            zone=server.get('gcp_zone', config.GCP_ZONE),
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
            
            # Get current user data
            user_data = {
                'id': str(current_user['_id']),
                'username': current_user['username'],
                'mollyPoints': current_user['mollyPoints']
            }
            
            return jsonify({
                'servers': valid_servers,
                'user': user_data
            })
        except Exception as e:
            logger.error(f"Error getting servers: {str(e)}")
            return jsonify({'message': f'Failed to retrieve servers: {str(e)}', 'servers': []}), 500
    
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
        if server_type == 'single' and current_user['mollyPoints'] < config.SINGLE_SERVER_COST:
            return jsonify({'message': f'Not enough Molly points! You need {config.SINGLE_SERVER_COST} points for a single server.'}), 400
        
        if server_type == 'unlimited' and current_user['mollyPoints'] < config.UNLIMITED_SERVER_COST:
            return jsonify({'message': f'Not enough Molly points! You need {config.UNLIMITED_SERVER_COST} points for unlimited servers.'}), 400
        
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
            # Use specific custom image
            custom_image_name = config.GCP_CUSTOM_IMAGE
            custom_image_project = config.GCP_IMAGE_PROJECT
            
            logger.info(f"Creating GCP instance for user {current_user['username']} using image {custom_image_name} from project {custom_image_project}")
            
            # Create a name for the VM
            vm_name = f"molly-server-{current_user['username']}-{uuid.uuid4().hex[:8]}".lower()
            
            # Create the disk configuration
            disk = compute_v1.AttachedDisk()
            disk.boot = True
            disk.auto_delete = True
            
            initialize_params = compute_v1.AttachedDiskInitializeParams()
            
            # Try different image sources
            try_image_sources = [
                f"projects/{custom_image_project}/global/images/{custom_image_name}",
                "projects/debian-cloud/global/images/family/debian-11"
            ]
            
            # Try to find a valid image
            valid_image = None
            for image_source in try_image_sources:
                try:
                    logger.info(f"Trying image source: {image_source}")
                    initialize_params.source_image = image_source
                    valid_image = image_source
                    break
                except Exception as e:
                    logger.warning(f"Image source {image_source} not valid: {str(e)}")
            
            if not valid_image:
                raise Exception("No valid disk image found. Please check your image name and project.")
                
            initialize_params.source_image = valid_image
            initialize_params.disk_size_gb = config.DISK_SIZE_GB
            initialize_params.disk_type = f"zones/{config.GCP_ZONE}/diskTypes/{config.DISK_TYPE}"
            
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
            
            # Create startup script
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
            instance.machine_type = f"zones/{config.GCP_ZONE}/machineTypes/{config.MACHINE_TYPE}"
            instance.disks = [disk]
            instance.network_interfaces = [network_interface]
            instance.metadata = metadata
            
            # Set labels
            instance.labels = {
                "user": current_user['username'].lower(),
                "servertype": server_type,
                "service": "mollyserver"
            }
            
            # Create the instance
            operation = gcp_clients.instance_client.insert(
                project=config.GCP_PROJECT_ID,
                zone=config.GCP_ZONE,
                instance_resource=instance
            )
            
            # Wait for the create operation to complete
            wait_for_operation(
                gcp_clients.operation_client, 
                operation, 
                config.GCP_PROJECT_ID, 
                config.GCP_ZONE
            )
            
            # Get the created instance
            created_instance = gcp_clients.instance_client.get(
                project=config.GCP_PROJECT_ID,
                zone=config.GCP_ZONE,
                instance=vm_name
            )
            
            # Get the public IP address
            public_ip = None
            for interface in created_instance.network_interfaces:
                for access_config in interface.access_configs:
                    if access_config.name == "External NAT":
                        public_ip = access_config.nat_i_p
                        break
            
            if not public_ip:
                raise Exception("Failed to get public IP address for the instance")
            
            logger.info(f"GCP instance {vm_name} is running with IP {public_ip}")
            
            # For unlimited servers, allocate a unique static IP
            if server_type == 'unlimited':
                try:
                    # Get a fresh static IP using our new function
                    static_address = get_fresh_static_ip(
                        db=db,
                        gcp_clients=gcp_clients,
                        user_id=str(current_user['_id']),
                        instance_id=vm_name,
                        project_id=config.GCP_PROJECT_ID,
                        region=config.GCP_REGION,
                        max_attempts=config.IP_ROTATION_MAX_ATTEMPTS
                    )
                    
                    # Delete the existing access config
                    operation = gcp_clients.instance_client.delete_access_config(
                        project=config.GCP_PROJECT_ID,
                        zone=config.GCP_ZONE,
                        instance=vm_name,
                        access_config=access_config.name,
                        network_interface="nic0"
                    )
                    
                    wait_for_operation(
                        gcp_clients.operation_client, 
                        operation, 
                        config.GCP_PROJECT_ID, 
                        config.GCP_ZONE
                    )
                    
                    # Add a new access config with the static IP
                    access_config = compute_v1.AccessConfig()
                    access_config.name = "External NAT"
                    access_config.type_ = "ONE_TO_ONE_NAT"
                    
                    # Try different field names that might be correct for your version of the API
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
                    
                    operation = gcp_clients.instance_client.add_access_config(
                        project=config.GCP_PROJECT_ID,
                        zone=config.GCP_ZONE,
                        instance=vm_name,
                        network_interface="nic0",
                        access_config_resource=access_config
                    )
                    
                    wait_for_operation(
                        gcp_clients.operation_client, 
                        operation, 
                        config.GCP_PROJECT_ID, 
                        config.GCP_ZONE
                    )
                    
                    # Update the public IP
                    public_ip = static_address.address
                    logger.info(f"Assigned fresh static IP {public_ip} to unlimited server {vm_name}")
                
                except Exception as ip_error:
                    logger.error(f"Error allocating static IP: {str(ip_error)}")
                    # Continue with the ephemeral IP if we can't get a static one
                    logger.info(f"Using ephemeral IP {public_ip} for server {vm_name} due to error")
            
            # Calculate expiry time based on server type
            if server_type == 'single':
                expires_in = datetime.timedelta(hours=config.SINGLE_SERVER_DURATION_HOURS)
                cost = config.SINGLE_SERVER_COST
            else:  # unlimited
                expires_in = datetime.timedelta(hours=config.UNLIMITED_SERVER_DURATION_HOURS)
                cost = config.UNLIMITED_SERVER_COST
            
            expires_at = datetime.datetime.now(datetime.UTC) + expires_in
            
            # Save server details to database
            server = {
                'user_id': current_user['_id'],
                'instanceId': vm_name,
                'ip': public_ip,
                'type': server_type,
                'createdAt': datetime.datetime.now(datetime.UTC),
                'expiresAt': expires_at,
                'gcp_zone': config.GCP_ZONE,
                'gcp_region': config.GCP_REGION,
                'status': 'starting'
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
                    operation = gcp_clients.instance_client.delete(
                        project=config.GCP_PROJECT_ID,
                        zone=config.GCP_ZONE,
                        instance=vm_name
                    )
                    wait_for_operation(
                        gcp_clients.operation_client, 
                        operation, 
                        config.GCP_PROJECT_ID, 
                        config.GCP_ZONE
                    )
                except Exception as terminate_error:
                    logger.error(f"Error terminating GCP instance after failure: {str(terminate_error)}")
            
            return jsonify({'message': f'Failed to create server: {str(e)}'}), 500
    
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
                    instance_info = gcp_clients.instance_client.get(
                        project=config.GCP_PROJECT_ID,
                        zone=server.get('gcp_zone', config.GCP_ZONE),
                        instance=server['instanceId']
                    )
                    
                    # Check if this is an unlimited server with a static IP
                    if server.get('type') == 'unlimited':
                        try:
                            # Find static IPs that match this server's IP
                            static_ips = []
                            for address in gcp_clients.addresses_client.list(
                                project=config.GCP_PROJECT_ID,
                                region=server.get('gcp_region', config.GCP_REGION)
                            ):
                                # Filter the addresses manually
                                if address.address == server["ip"]:
                                    static_ips.append(address)
                            
                            # Release any associated static IPs
                            for address in static_ips:
                                logger.info(f"Releasing static IP {address.address} (Name: {address.name})")
                                operation = gcp_clients.addresses_client.delete(
                                    project=config.GCP_PROJECT_ID,
                                    region=server.get('gcp_region', config.GCP_REGION),
                                    address=address.name
                                )
                                wait_for_regional_operation(
                                    gcp_clients.region_operation_client, 
                                    operation, 
                                    config.GCP_PROJECT_ID, 
                                    server.get('gcp_region', config.GCP_REGION)
                                )
                        except Exception as static_ip_error:
                            logger.error(f"Error releasing static IP: {str(static_ip_error)}")
                            # Continue anyway to make sure we terminate the instance
                    
                    # Only terminate if the instance is not already terminated
                    if instance_info.status not in ['TERMINATED', 'STOPPING']:
                        # Terminate the GCP instance
                        operation = gcp_clients.instance_client.delete(
                            project=config.GCP_PROJECT_ID,
                            zone=server.get('gcp_zone', config.GCP_ZONE),
                            instance=server['instanceId']
                        )
                        wait_for_operation(
                            gcp_clients.operation_client, 
                            operation, 
                            config.GCP_PROJECT_ID, 
                            server.get('gcp_zone', config.GCP_ZONE)
                        )
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
                
                instance_info = gcp_clients.instance_client.get(
                    project=config.GCP_PROJECT_ID,
                    zone=server.get('gcp_zone', config.GCP_ZONE),
                    instance=instance_id
                )
                
                # Continuation of routes.py file

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
                
                # Get a fresh static IP using our new function
                try:
                    static_address = get_fresh_static_ip(
                        db=db,
                        gcp_clients=gcp_clients,
                        user_id=str(current_user['_id']),
                        instance_id=instance_id,
                        project_id=config.GCP_PROJECT_ID,
                        region=server.get('gcp_region', config.GCP_REGION),
                        max_attempts=config.IP_ROTATION_MAX_ATTEMPTS
                    )
                    
                    # Update the instance to use the new static IP
                    # First, delete the existing access config
                    operation = gcp_clients.instance_client.delete_access_config(
                        project=config.GCP_PROJECT_ID,
                        zone=server.get('gcp_zone', config.GCP_ZONE),
                        instance=instance_id,
                        access_config=access_config_name,
                        network_interface="nic0"
                    )
                    
                    wait_for_operation(
                        gcp_clients.operation_client, 
                        operation, 
                        config.GCP_PROJECT_ID, 
                        server.get('gcp_zone', config.GCP_ZONE)
                    )
                    
                    # Then, add a new access config with the static IP
                    access_config = compute_v1.AccessConfig()
                    access_config.name = "External NAT"
                    access_config.type_ = "ONE_TO_ONE_NAT"
                    
                    # Try different field names that might be correct for your version of the API
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
                    
                    operation = gcp_clients.instance_client.add_access_config(
                        project=config.GCP_PROJECT_ID,
                        zone=server.get('gcp_zone', config.GCP_ZONE),
                        instance=instance_id,
                        network_interface="nic0",
                        access_config_resource=access_config
                    )
                    
                    wait_for_operation(
                        gcp_clients.operation_client, 
                        operation, 
                        config.GCP_PROJECT_ID, 
                        server.get('gcp_zone', config.GCP_ZONE)
                    )
                    
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
                        server.get('gcp_zone', config.GCP_ZONE),
                        server.get('gcp_region', config.GCP_REGION)
                    )
                    
                    return jsonify({
                        'message': 'IP rotation initiated! New fresh IP will be ready shortly.',
                        'server': server_data
                    })
                    
                except Exception as ip_error:
                    logger.error(f"Error allocating fresh static IP: {str(ip_error)}")
                    return jsonify({'message': f'Failed to allocate a fresh IP: {str(ip_error)}'}), 500
                    
            except Exception as client_error:
                logger.error(f"Google Cloud client error: {str(client_error)}")
                return jsonify({'message': f'Google Cloud client error: {str(client_error)}'}), 500
        except Exception as e:
            logger.error(f"Error in rotate_ip: {str(e)}")
            return jsonify({'message': f'Failed to rotate server IP: {str(e)}'}), 500
    
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
                instance_info = gcp_clients.instance_client.get(
                    project=config.GCP_PROJECT_ID,
                    zone=server.get('gcp_zone', config.GCP_ZONE),
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
                run_service_check_in_background(
                    server['ip'], 
                    server['_id'],
                    max_attempts=15, 
                    retry_delay=5
                )
                server_data['status'] = 'rechecking'
                
            return jsonify({'server': server_data})
        
        except Exception as e:
            logger.error(f"Error checking server status: {str(e)}")
            return jsonify({'message': f'Failed to check server status: {str(e)}'}), 500

    # Add a route to view used IPs (for admin users only)
    @app.route('/api/admin/used-ips', methods=['GET'])
    @token_required
    def view_used_ips(current_user):
        # Only allow admin users
        if current_user.get('role') != 'admin':
            return jsonify({'message': 'Unauthorized!'}), 401
            
        try:
            # Get all used IPs
            used_ips = list(db.UsedIPs.find({}, {'_id': 0}))
            
            # Convert ObjectId and datetime to strings for JSON serialization
            for ip in used_ips:
                if 'lastAssignedAt' in ip:
                    ip['lastAssignedAt'] = ip['lastAssignedAt'].isoformat()
                if 'lastUsedBy' in ip and isinstance(ip['lastUsedBy'], ObjectId):
                    ip['lastUsedBy'] = str(ip['lastUsedBy'])
            
            return jsonify({
                'count': len(used_ips),
                'ips': used_ips
            })
        except Exception as e:
            logger.error(f"Error viewing used IPs: {str(e)}")
            return jsonify({'message': f'Failed to retrieve used IPs: {str(e)}'}), 500