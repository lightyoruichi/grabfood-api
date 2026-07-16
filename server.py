from flask import Flask, jsonify, request, send_from_directory, send_file
from flask_cors import CORS
from grab_api_client import GrabFoodClient
from grab_playwright_service import GrabPlaywrightService
import threading
import logging
import os
import time

app = Flask(__name__)
CORS(app) # Enable CORS for all routes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Default coordinates for token refresh (Kuala Lumpur)
DEFAULT_LAT = 3.1390
DEFAULT_LNG = 101.6869

# Initialize client with refresh callback
def refresh_tokens_callback():
    """Callback function to refresh tokens"""
    try:
        service = GrabPlaywrightService(headless=True)
        ctx = service.get_auth_context(DEFAULT_LAT, DEFAULT_LNG)
        if ctx.get('headers'):
            logger.info("Background token refresh successful")
            return True
        else:
            logger.warning("Background token refresh failed - no headers captured")
            return False
    except Exception as e:
        logger.exception(f"Background token refresh error: {e}")
        return False

client = GrabFoodClient(refresh_callback=refresh_tokens_callback)

# Background token refresh worker
def token_refresh_worker():
    """Background thread to refresh tokens periodically"""
    # Perform immediate refresh on startup
    try:
        logger.info("Performing initial token refresh on startup...")
        refresh_tokens_callback()
        # Reload client headers after refresh
        client.headers = client._get_headers()
        logger.info("Initial token refresh completed successfully")
    except Exception as e:
        logger.exception(f"Initial token refresh failed: {e}")
    
    # Enter hourly refresh loop
    while True:
        try:
            # Refresh every hour (3600 seconds)
            time.sleep(3600)
            logger.info("Starting scheduled token refresh...")
            refresh_tokens_callback()
            # Reload client headers after refresh
            client.headers = client._get_headers()
        except Exception as e:
            logger.exception(f"Token refresh worker error: {e}")
            # Use short retry delay on error (60 seconds) for quick recovery
            logger.info("Retrying token refresh in 60 seconds...")
            time.sleep(60)

# Start background token refresh thread
token_refresh_thread = threading.Thread(target=token_refresh_worker, daemon=True)
token_refresh_thread.start()
logger.info("Background token refresh worker started")

@app.route('/')
def home():
    """Serve the frontend explorer"""
    file_path = os.path.join(os.getcwd(), 'index.html')
    if os.path.exists(file_path):
        return send_file(file_path)
    return "index.html not found", 404

@app.route('/favicon.ico')
def favicon():
    """Serve favicon requests with no content to prevent console 404 errors"""
    return '', 204

def update_tokens_background(lat, lng):
    """
    Helper to run Playwright in background if needed.
    """
    service = GrabPlaywrightService(headless=True)
    service.get_auth_context(lat, lng)

@app.route('/api/refresh-token', methods=['POST'])
def refresh_token():
    """
    Endpoint to force refresh tokens via Playwright.
    Expects JSON: { "lat": 123.45, "lng": 67.89 } (optional, uses defaults if not provided)
    """
    try:
        data = request.json or {}
        lat = data.get('lat', DEFAULT_LAT)
        lng = data.get('lng', DEFAULT_LNG)
        
        # Validate coordinates if provided
        try:
            lat = float(lat)
            lng = float(lng)
            if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
                return jsonify({
                    "error": "Invalid coordinates",
                    "message": "Latitude must be between -90 and 90, longitude must be between -180 and 180"
                }), 400
        except (ValueError, TypeError):
            return jsonify({
                "error": "Invalid coordinate format",
                "message": "Latitude and longitude must be valid numbers"
            }), 400
        
        # Run synchronously to ensure token is ready
        service = GrabPlaywrightService(headless=True)
        ctx = service.get_auth_context(lat, lng)
        if ctx.get('headers'):
            # Reload client headers after refresh
            client.headers = client._get_headers()
            return jsonify({
                "status": "success",
                "message": "Tokens refreshed successfully"
            }), 200
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to capture tokens"
            }), 500
    except Exception as e:
        logger.error(f"Token refresh failed: {e}", exc_info=True)
        return jsonify({
            "error": "Token refresh failed",
            "message": str(e)
        }), 500

@app.route('/api/restaurants', methods=['GET'])
def get_restaurants():
    """
    Get restaurants endpoint with improved error handling and validation.
    
    Query parameters:
        lat (required): Latitude (-90 to 90)
        lng (required): Longitude (-180 to 180)
        keyword (optional): Search keyword (default: "food")
        limit (optional): Maximum results (default: 32)
        use_cache (optional): Use cached results (default: true)
    """
    lat = request.args.get('lat')
    lng = request.args.get('lng')
    
    if not lat or not lng:
        return jsonify({"error": "Missing lat or lng parameters"}), 400
    
    try:
        # Validate and convert coordinates
        lat = float(lat)
        lng = float(lng)
        
        # Validate coordinate ranges
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            return jsonify({
                "error": "Invalid coordinates",
                "message": "Latitude must be between -90 and 90, longitude must be between -180 and 180"
            }), 400
        
        # Get optional parameters
        keyword = request.args.get('keyword', 'food')
        limit = request.args.get('limit', '32')
        use_cache = request.args.get('use_cache', 'true').lower() == 'true'
        
        # Validate limit
        try:
            limit = int(limit)
            if limit < 1 or limit > 100:
                return jsonify({
                    "error": "Invalid limit",
                    "message": "Limit must be between 1 and 100"
                }), 400
        except ValueError:
            return jsonify({
                "error": "Invalid limit format",
                "message": "Limit must be a valid integer"
            }), 400
        
        # Use the Authenticated Search Endpoint
        merchants = client.search_restaurants(lat, lng, keyword=keyword, limit=limit, use_cache=use_cache)
        
        results = []
        for m in merchants:
            brief = m.get('merchantBrief', {})
            display = brief.get('displayInfo', {})
            address = m.get('address', {})
            
            # Extract name with fallback
            name = display.get('primaryText') or address.get('name')
            
            results.append({
                "id": m.get('id'),
                "name": name,
                "latitude": m.get('latlng', {}).get('latitude'),
                "longitude": m.get('latlng', {}).get('longitude'),
                "rating": brief.get('rating'),
                "cuisine": brief.get('cuisine'),
                "photo": brief.get('photoHref'),
                "status": m.get('merchantStatusInfo', {}).get('status'),
                "distance": brief.get('distanceInKm'),
                "price": brief.get('priceTag'), # Usually 1, 2, 3 ($, $$, $$$)
                "link": f"https://food.grab.com/my/en/restaurant/{slugify(name)}/{m.get('id')}?"
            })
            
        return jsonify({
            "restaurants": results,
            "count": len(results)
        })
        
    except ValueError as e:
        logger.error(f"Invalid coordinate format: {e}")
        return jsonify({
            "error": "Invalid coordinate format",
            "message": "Latitude and longitude must be valid numbers"
        }), 400
    except Exception as e:
        logger.error(f"Search failed: {e}", exc_info=True)
        return jsonify({
            "error": "Internal server error",
            "message": str(e)
        }), 500

def slugify(text):
    import re
    text = text.lower()
    # Replace non-alphanumeric (except dashes and spaces) with nothing
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    # Replace whitespace and dashes with a single dash
    text = re.sub(r'[\s-]+', '-', text)
    # Strip leading/trailing dashes
    text = text.strip('-')
    # Append -delivery if not present (simple heuristic based on user feedback)
    if not text.endswith('-delivery'):
        text += '-delivery'
    return text

if __name__ == '__main__':
    print("Starting GrabFood API Server on port 5001...")
    app.run(debug=True, port=5001)
