import logging
import datetime
from bson.objectid import ObjectId
from gcp_utils import wait_for_operation, wait_for_regional_operation

# Setup logging
logger = logging.getLogger(__name__)

def terminate_expired_servers(db, gcp_clients, config):
    """
    Find and terminate servers that have expired
    """
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
                        # Find any static IPs associated with this server
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
                            logger.info(f"Releasing static IP {address.address} (Name: {address.name}) from expired server")
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
                        logger.error(f"Error releasing static IP for expired server: {str(static_ip_error)}")
                
                # Check instance state before attempting to terminate
                try:
                    instance_info = gcp_clients.instance_client.get(
                        project=config.GCP_PROJECT_ID,
                        zone=server.get('gcp_zone', config.GCP_ZONE),
                        instance=server['instanceId']
                    )
                    
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