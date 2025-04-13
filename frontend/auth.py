import jwt
import logging
from functools import wraps
from flask import request, jsonify, current_app
from bson.objectid import ObjectId

# Setup logging
logger = logging.getLogger(__name__)

def token_required(f):
    """
    Middleware for JWT token verification
    
    This decorator verifies that a valid JWT token is provided with the request.
    It also extracts the user information from the token and passes it to the decorated function.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        # Get the database connection from Flask's application context
        if 'db' not in current_app.config:
            logger.error("Database connection not available in app.config")
            return jsonify({'message': 'Server configuration error'}), 500
            
        db = current_app.config['db']
        
        token = None
        
        # Get token from Authorization header
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
        
        # Get token from query parameters
        if not token and 'token' in request.args:
            token = request.args.get('token')
        
        # Get token from form data
        if not token and request.form and 'token' in request.form:
            token = request.form.get('token')
            
        # Get token from JSON body
        if not token and request.is_json:
            json_data = request.get_json()
            if json_data and 'token' in json_data:
                token = json_data.get('token')
        
        # Return error if no token is found
        if not token:
            return jsonify({'message': 'Token is missing!'}), 401
        
        try:
            # Use the app's secret key to decode the token
            secret_key = current_app.config['SECRET_KEY']
            data = jwt.decode(token, secret_key, algorithms=["HS256"])
            
            # Get the user from the database
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
        
        # Call the decorated function with the user
        return f(current_user, *args, **kwargs)
    
    return decorated

def create_token(user_id, expiry_hours=24):
    """
    Create a JWT token for a user
    
    Args:
        user_id: The ID of the user
        expiry_hours: Hours until token expiration (default: 24)
        
    Returns:
        str: JWT token
    """
    import datetime
    from flask import current_app
    
    token = jwt.encode({
        'user_id': str(user_id),
        'exp': datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=expiry_hours)
    }, current_app.config['SECRET_KEY'], algorithm="HS256")
    
    return token