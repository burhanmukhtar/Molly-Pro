import logging
import time
from google.cloud import compute_v1
import requests

# Setup logging
logger = logging.getLogger(__name__)

def wait_for_operation(operation_client, operation, project_id, zone):
    """
    Wait for a GCP zonal operation to complete
    
    Args:
        operation_client: GCP zonal operations client
        operation: The operation to wait for
        project_id: GCP project ID
        zone: The zone where the operation is running
        
    Returns:
        The final operation status
    """
    logger.info(f"Waiting for zonal operation {operation.name} to complete")
    
    while True:
        # Get the operation status
        result = operation_client.get(
            project=project_id,
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

def wait_for_regional_operation(region_operation_client, operation, project_id, region):
    """
    Wait for a GCP regional operation to complete
    
    Args:
        region_operation_client: GCP regional operations client
        operation: The operation to wait for
        project_id: GCP project ID
        region: The region where the operation is running
        
    Returns:
        The final operation status
    """
    logger.info(f"Waiting for regional operation {operation.name} to complete")
    
    while True:
        # Get the operation status
        result = region_operation_client.get(
            project=project_id,
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

def wait_for_global_operation(global_operation_client, operation, project_id):
    """
    Wait for a GCP global operation to complete
    
    Args:
        global_operation_client: GCP global operations client
        operation: The operation to wait for
        project_id: GCP project ID
        
    Returns:
        The final operation status
    """
    logger.info(f"Waiting for global operation {operation.name} to complete")
    
    while True:
        # Get the operation status
        result = global_operation_client.get(
            project=project_id,
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

def ensure_firewall_rules(firewall_client, global_operation_client, project_id):
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
        request = firewall_client.list(project=project_id)
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
                    project=project_id,
                    firewall_resource=firewall_rule
                )
                
                # Wait for the operation to complete - use global operations client
                wait_for_global_operation(global_operation_client, operation, project_id)
                
                logger.info(f"Firewall rule {rule_config['name']} created successfully")
            else:
                logger.info(f"Firewall rule {rule_config['name']} already exists")
                
    except Exception as e:
        logger.error(f"Error ensuring firewall rules: {str(e)}")
        # Don't raise exception - application should still start even if firewall rules couldn't be created