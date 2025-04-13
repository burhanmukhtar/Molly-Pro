import logging
import datetime
import uuid
from google.cloud import compute_v1

# Setup logging
logger = logging.getLogger(__name__)

def ensure_ip_tracking_collection(db):
    """
    Create the UsedIPs collection if it doesn't exist
    """
    if 'UsedIPs' not in db.list_collection_names():
        db.create_collection('UsedIPs')
        # Create an index on the IP field for faster lookups
        db.UsedIPs.create_index('ip', unique=True)
        logger.info("Created UsedIPs collection for tracking used IP addresses")
    else:
        logger.info("UsedIPs collection already exists")

def is_ip_used(db, ip_address):
    """
    Check if an IP address has been used before
    
    Args:
        db: MongoDB database connection
        ip_address: IP address to check
        
    Returns:
        bool: True if IP has been used before, False otherwise
    """
    result = db.UsedIPs.find_one({'ip': ip_address})
    is_used = result is not None
    
    if is_used:
        logger.info(f"IP {ip_address} has been used before by user {result.get('lastUsedBy', 'unknown')}")
    else:
        logger.info(f"IP {ip_address} has not been used before")
        
    return is_used

def mark_ip_as_used(db, ip_address, user_id, instance_id):
    """
    Mark an IP address as used
    
    Args:
        db: MongoDB database connection
        ip_address: IP address to mark
        user_id: ID of the user who is using this IP
        instance_id: ID of the instance using this IP
    """
    db.UsedIPs.update_one(
        {'ip': ip_address},
        {'$set': {
            'ip': ip_address,
            'lastUsedBy': str(user_id),
            'lastInstanceId': instance_id,
            'lastAssignedAt': datetime.datetime.now(datetime.UTC)
        },
        '$inc': {
            'usageCount': 1  # Increment usage count if already exists
        }},
        upsert=True
    )
    logger.info(f"Marked IP {ip_address} as used by user {user_id}, instance {instance_id}")

def get_fresh_static_ip(db, gcp_clients, user_id, instance_id, project_id, region, max_attempts=10):
    """
    Get a fresh static IP that hasn't been used before
    
    Args:
        db: MongoDB database connection
        gcp_clients: Object containing GCP client instances
        user_id: ID of the user requesting the IP
        instance_id: ID of the instance to use the IP
        project_id: GCP project ID
        region: GCP region
        max_attempts: Maximum number of attempts to get a fresh IP
        
    Returns:
        The static address object with a fresh IP
        
    Raises:
        Exception: If unable to get a fresh IP after max_attempts
    """
    for attempt in range(max_attempts):
        try:
            # Create a name for the new static IP
            new_static_ip_name = f"molly-ip-{uuid.uuid4().hex[:8]}".lower()
            
            # Create a new static IP address in the region
            address = compute_v1.Address()
            address.name = new_static_ip_name
            address.description = f"Static IP for user {user_id}"
            
            # Create the address
            operation = gcp_clients.addresses_client.insert(
                project=project_id,
                region=region,
                address_resource=address
            )
            
            # Wait for the create operation to complete
            from gcp_utils import wait_for_regional_operation
            wait_for_regional_operation(gcp_clients.region_operation_client, operation, project_id, region)
            
            # Get the created address
            static_address = gcp_clients.addresses_client.get(
                project=project_id,
                region=region,
                address=new_static_ip_name
            )
            
            # Check if this IP has been used before
            if not is_ip_used(db, static_address.address):
                # Mark this IP as used
                mark_ip_as_used(db, static_address.address, user_id, instance_id)
                logger.info(f"Assigned fresh IP {static_address.address} to user {user_id}")
                return static_address
            else:
                # Release this IP since it's been used before
                logger.info(f"IP {static_address.address} has been used before, releasing and trying again")
                operation = gcp_clients.addresses_client.delete(
                    project=project_id,
                    region=region,
                    address=new_static_ip_name
                )
                wait_for_regional_operation(gcp_clients.region_operation_client, operation, project_id, region)
        except Exception as e:
            logger.error(f"Error getting fresh IP (attempt {attempt+1}/{max_attempts}): {str(e)}")
            if attempt == max_attempts - 1:
                raise  # Re-raise the exception on the last attempt
            
    # If we couldn't get a fresh IP after max attempts, raise an exception
    raise Exception(f"Failed to get a fresh IP after {max_attempts} attempts")

def initialize_ip_tracking(db):
    """
    Initialize the IP tracking system
    """
    ensure_ip_tracking_collection(db)
    logger.info("IP tracking system initialized")