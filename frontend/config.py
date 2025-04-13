import os

class Config:
    # Flask configuration
    DEFAULT_SECRET_KEY = 'dev_secret_key'  # Should be overridden by environment variable
    
    # MongoDB configuration
    MONGO_USERNAME = "bazhar691"
    MONGO_PASSWORD = "Naruto@007"
    MONGO_HOST = "voidmailer.4ivgowc.mongodb.net"
    MONGO_DB = "VoidMailer"
    
    # Google Cloud configuration
    GOOGLE_APPLICATION_CREDENTIALS = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', './molly-pro-452801-5272e06a4346.json')
    GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID', 'server-config-d')
    GCP_ZONE = os.environ.get('GCP_ZONE', 'us-central1-c')
    GCP_REGION = os.environ.get('GCP_REGION', 'us-central1')
    GCP_CUSTOM_IMAGE = os.environ.get('GCP_CUSTOM_IMAGE', 'molly-js')
    GCP_IMAGE_PROJECT = os.environ.get('GCP_IMAGE_PROJECT', GCP_PROJECT_ID)
    
    # Admin configuration
    ADMIN_API_KEY = os.environ.get('ADMIN_API_KEY', 'admin_secret_key')
    
    # Server configurations
    SINGLE_SERVER_DURATION_HOURS = 2
    UNLIMITED_SERVER_DURATION_HOURS = 12
    SINGLE_SERVER_COST = 100
    UNLIMITED_SERVER_COST = 2000
    
    # VM Configuration
    MACHINE_TYPE = "e2-standard-2"  # Equivalent to t2.medium
    DISK_SIZE_GB = 10
    DISK_TYPE = "pd-ssd"
    
    # Service check configuration
    SERVICE_CHECK_MAX_ATTEMPTS = 30
    SERVICE_CHECK_RETRY_DELAY = 10
    
    # IP Rotation configuration
    IP_ROTATION_MAX_ATTEMPTS = 10