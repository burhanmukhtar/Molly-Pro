from flask import Flask, send_from_directory, jsonify
from flask_cors import CORS
import os
import logging
import certifi
import urllib.parse
import datetime
from pymongo import MongoClient
from google.oauth2 import service_account
from google.cloud import compute_v1
from google.auth import exceptions

# Import our custom modules
from config import Config
from ip_tracking import initialize_ip_tracking
from gcp_utils import ensure_firewall_rules
from maintenance import terminate_expired_servers
from routes import register_routes

# Configure app logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def create_app():
    """
    Create and configure the Flask application
    """
    # Create Flask app
    app = Flask(__name__, static_folder='static', template_folder='templates')
    CORS(app)
    
    # Configuration
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', Config.DEFAULT_SECRET_KEY)
    
    # Setup MongoDB connection
    username = urllib.parse.quote_plus(Config.MONGO_USERNAME)
    password = urllib.parse.quote_plus(Config.MONGO_PASSWORD)
    
    mongo_uri = f"mongodb+srv://{username}:{password}@{Config.MONGO_HOST}/{Config.MONGO_DB}?retryWrites=true&w=majority&connectTimeoutMS=30000&socketTimeoutMS=30000&serverSelectionTimeoutMS=30000&maxIdleTimeMS=45000"
    
    client = MongoClient(
        mongo_uri,
        tlsCAFile=certifi.where(),
        connect=False,
        connectTimeoutMS=30000,
        socketTimeoutMS=30000,
        serverSelectionTimeoutMS=30000,
        maxIdleTimeMS=45000
    )
    db = client[Config.MONGO_DB]
    
    app.config['db'] = db
    
    # Create a GCPClients class to be used before initialization
    class GCPClients:
        instance_client = None
        addresses_client = None
        global_addresses_client = None
        instance_groups_client = None
        operation_client = None
        region_operation_client = None
        firewall_client = None
        global_operation_client = None
    
    # Initialize Google Cloud clients
    try:
        if os.path.exists(Config.GOOGLE_APPLICATION_CREDENTIALS):
            credentials = service_account.Credentials.from_service_account_file(
                Config.GOOGLE_APPLICATION_CREDENTIALS
            )
            
            # Create Google Cloud Compute Engine clients
            GCPClients.instance_client = compute_v1.InstancesClient(credentials=credentials)
            GCPClients.addresses_client = compute_v1.AddressesClient(credentials=credentials)
            GCPClients.global_addresses_client = compute_v1.GlobalAddressesClient(credentials=credentials)
            GCPClients.instance_groups_client = compute_v1.InstanceGroupsClient(credentials=credentials)
            GCPClients.operation_client = compute_v1.ZoneOperationsClient(credentials=credentials)
            GCPClients.region_operation_client = compute_v1.RegionOperationsClient(credentials=credentials)
            GCPClients.firewall_client = compute_v1.FirewallsClient(credentials=credentials)
            GCPClients.global_operation_client = compute_v1.GlobalOperationsClient(credentials=credentials)
        else:
            # Use application default credentials
            GCPClients.instance_client = compute_v1.InstancesClient()
            GCPClients.addresses_client = compute_v1.AddressesClient()
            GCPClients.global_addresses_client = compute_v1.GlobalAddressesClient()
            GCPClients.instance_groups_client = compute_v1.InstanceGroupsClient()
            GCPClients.operation_client = compute_v1.ZoneOperationsClient()
            GCPClients.region_operation_client = compute_v1.RegionOperationsClient()
            GCPClients.firewall_client = compute_v1.FirewallsClient()
            GCPClients.global_operation_client = compute_v1.GlobalOperationsClient()
            
        logger.info("Successfully initialized Google Cloud clients")
    except exceptions.DefaultCredentialsError as e:
        logger.error(f"Failed to authenticate with Google Cloud: {str(e)}")
        raise
    
    # Serve static files
    @app.route('/static/<path:path>')
    def serve_static(path):
        return send_from_directory('static', path)
    
    # Serve the templates
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
        
    # Register all routes
    register_routes(app, db, GCPClients, Config)
    
    # Initialize IP tracking system
    initialize_ip_tracking(db)
    
    # Ensure required firewall rules exist
    ensure_firewall_rules(GCPClients.firewall_client, 
                          GCPClients.global_operation_client, 
                          Config.GCP_PROJECT_ID)
    
    # Run scheduled task once at startup
    terminate_expired_servers(db, GCPClients, Config)
    
    return app

if __name__ == '__main__':
    # Create and run the Flask app
    app = create_app()
    app.run(debug=False, host='0.0.0.0', port=80)