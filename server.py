from flask import Flask, jsonify, request, send_from_directory, send_file
from flask_cors import CORS
from grab_api_client import GrabFoodClient
from grab_selenium_service import GrabSeleniumService
import threading
import logging
import os

app = Flask(__name__)
CORS(app) # Enable CORS for all routes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize client (defaults to MY, can be dynamic)
client = GrabFoodClient()

@app.route('/')
def home():
    """Serve the frontend explorer"""
    file_path = os.path.join(os.getcwd(), 'index.html')
    if os.path.exists(file_path):
        return send_file(file_path)
    return "index.html not found", 404

def update_tokens_background(lat, lng):
    """
    Helper to run Selenium in background if needed.
    """
    service = GrabSeleniumService(headless=True)
    service.get_auth_context(lat, lng)

@app.route('/api/refresh-token', methods=['POST'])
def refresh_token():
    """
    Endpoint to force refresh tokens via Selenium.
    Expects JSON: { "lat": 123.45, "lng": 67.89 }
    """
    data = request.json
    lat = data.get('lat')
    lng = data.get('lng')
    
    if not lat or not lng:
        return jsonify({"error": "Missing lat/lng"}), 400
        
    try:
        # Run synchronously for now to ensure token is ready
        service = GrabSeleniumService(headless=True)
        ctx = service.get_auth_context(lat, lng)
        if ctx.get('headers'):
            return jsonify({"status": "success", "message": "Tokens refreshed"}), 200
        else:
            return jsonify({"status": "error", "message": "Failed to capture tokens"}), 500
    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/restaurants', methods=['GET'])
def get_restaurants():
    lat = request.args.get('lat')
    lng = request.args.get('lng')
    
    if not lat or not lng:
        return jsonify({"error": "Missing lat or lng parameters"}), 400
        
    try:
        # Get keyword from request, default to "food"
        keyword = request.args.get('keyword', 'food')
        
        # Use the Authenticated Search Endpoint
        merchants = client.search_restaurants(float(lat), float(lng), keyword=keyword)
        
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
            
        return jsonify({"restaurants": results, "count": len(results)})
    except Exception as e:
        logger.error(f"Search failed: {e}")
        return jsonify({"error": str(e)}), 500

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
