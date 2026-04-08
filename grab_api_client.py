import requests
import json
import os
import time
import random
import logging
from urllib.parse import quote
from datetime import datetime, timedelta
from threading import Lock

logger = logging.getLogger(__name__)

class GrabFoodClient:
    def __init__(self, refresh_callback=None):
        self.BASE_URL = "https://portal.grab.com/foodweb/v2"
        self.GUEST_URL = "https://portal.grab.com/foodweb/guest/v2"
        self.refresh_callback = refresh_callback  # Callback to refresh tokens
        
        # Rate limiting
        self.last_request_time = 0
        self.min_request_interval = 0.5  # Minimum 500ms between requests
        self.rate_limit_lock = Lock()
        
        # Cache for restaurant searches
        self.cache = {}
        self.cache_lock = Lock()
        self.cache_ttl = 300  # 5 minutes default TTL
        
        # User agent rotation pool
        self.user_agents = [
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        ]
        
        # Initialize headers after user_agents is defined
        self.headers = self._get_headers()

    def _get_headers(self):
        # Default minimal headers with rotated user agent
        headers = {
            "User-Agent": random.choice(self.user_agents),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://food.grab.com",
            "Referer": "https://food.grab.com/",
            "x-country-code": "MY",
            "x-grab-web-app-version": "KiD91Esa2cUXp_nXkoalT" # Default fallback
        }
        
        # Load captured context if available
        if os.path.exists("grab_auth_context.json"):
            try:
                with open("grab_auth_context.json", "r") as f:
                    data = json.load(f)
                    
                    # 1. Merge Headers
                    captured_headers = data.get("headers", {})
                    for k, v in captured_headers.items():
                        # We want critical auth headers
                        if k.lower() in ['x-hydra-jwt', 'x-recaptcha-token', 'x-grab-web-app-version', 'user-agent', 'cookie']:
                            headers[k] = v
                    
                    # 2. Add Cookies to Cookie Header explicitly if needed
                    cookies = data.get("cookies", {})
                    if cookies:
                        # Check if it's a list (old format) or dict (new format)
                        if isinstance(cookies, list):
                            cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                        elif isinstance(cookies, dict):
                            cookie_str = "; ".join([f"{names}={values}" for names, values in cookies.items()])
                        else:
                            cookie_str = ""

                        # Append to existing cookie header or create new
                        if cookie_str:
                            if 'cookie' in headers:
                                headers['cookie'] += f"; {cookie_str}"
                            else:
                                headers['cookie'] = cookie_str
                            
                    logger.info("Loaded Authentication Context from grab_auth_context.json")
            except Exception as e:
                logger.exception(f"Failed to load auth context: {e}")
        
        return headers
    
    def _is_token_expired(self, response):
        """Check if the response indicates token expiry"""
        if response.status_code == 401 or response.status_code == 403:
            # Check response body for token-related errors
            try:
                error_data = response.json()
                error_msg = str(error_data).lower()
                if any(keyword in error_msg for keyword in ['token', 'unauthorized', 'forbidden', 'expired', 'invalid']):
                    return True
            except (json.JSONDecodeError, ValueError):
                # Parse failure still indicates token expiry for 401/403
                pass
            return True
        return False
    
    def refresh_tokens(self):
        """Refresh authentication tokens using the callback"""
        if self.refresh_callback:
            logger.info("Refreshing tokens via callback...")
            try:
                self.refresh_callback()
                # Reload headers after refresh
                self.headers = self._get_headers()
                logger.info("Tokens refreshed successfully")
                return True
            except Exception as e:
                logger.exception(f"Token refresh failed: {e}")
                return False
        return False
    
    def _rate_limit(self):
        """Enforce rate limiting between requests"""
        with self.rate_limit_lock:
            current_time = time.time()
            time_since_last = current_time - self.last_request_time
            if time_since_last < self.min_request_interval:
                sleep_time = self.min_request_interval - time_since_last
                time.sleep(sleep_time)
            self.last_request_time = time.time()
    
    def _get_cache_key(self, lat, lng, keyword):
        """Generate cache key for search parameters"""
        return f"{lat:.6f}_{lng:.6f}_{keyword.lower()}"
    
    def _get_cached_result(self, cache_key):
        """Get cached result if valid"""
        with self.cache_lock:
            if cache_key in self.cache:
                data, timestamp = self.cache[cache_key]
                if datetime.now() - timestamp < timedelta(seconds=self.cache_ttl):
                    logger.info(f"Cache hit for key: {cache_key}")
                    return data
                else:
                    # Expired, remove from cache
                    del self.cache[cache_key]
                    logger.info(f"Cache expired for key: {cache_key}")
        return None
    
    def _set_cached_result(self, cache_key, data):
        """Store result in cache"""
        with self.cache_lock:
            self.cache[cache_key] = (data, datetime.now())
            logger.info(f"Cached result for key: {cache_key}")

    def search_restaurants(self, lat, lng, keyword="food", limit=32, use_cache=True):
        """
        Search for restaurants using the Guest API (v2/category) and filter locally.
        This bypasses the need for x-recaptcha-token required by v2/search.
        
        Args:
            lat: Latitude
            lng: Longitude
            keyword: Search keyword (default: "food")
            limit: Maximum number of results (default: 32)
            use_cache: Whether to use cached results (default: True)
        """
        # Check cache first
        cache_key = self._get_cache_key(lat, lng, keyword)
        if use_cache:
            cached_result = self._get_cached_result(cache_key)
            if cached_result is not None:
                return cached_result[:limit]
        
        # Enforce rate limiting
        self._rate_limit()
        
        # Endpoint for guest category listing (works without strict auth)
        target_url = "https://portal.grab.com/foodweb/guest/v2/category"
        
        # We use a generic category ID to get a broad list. 
        # ID 7077 is often "Food Delivery" or similar generic category.
        params = {
            "latlng": f"{lat},{lng}",
            "categoryShortcutID": "7077", # Generic "Food" category
            "offset": "0",
            "pageSize": str(limit * 2), # Fetch more to allow for filtering
            "countryCode": "MY",
        }
        
        logger.info(f"Searching via Guest API: {target_url} with params {params}")
        
        try:
            response = requests.get(target_url, headers=self.headers, params=params, timeout=15)
            logger.info(f"Response Status: {response.status_code}")
            
            # Check for token expiry
            if self._is_token_expired(response):
                logger.warning("Token expired, attempting refresh...")
                if self.refresh_tokens():
                    # Retry request with new tokens
                    self._rate_limit()
                    response = requests.get(target_url, headers=self.headers, params=params, timeout=15)
                    logger.info(f"Retry Response Status: {response.status_code}")
                else:
                    logger.error("Token refresh failed")
                    return []
            
            if response.status_code != 200:
                logger.error(f"Error response: {response.text}")
                return []
                
            data = response.json()
            
            # The structure is usually data['searchResult']['searchMerchants'] 
            # OR data['merchantList'] depending on endpoint.
            merchants = []
            if isinstance(data, list):
                merchants = data
            elif 'searchResult' in data:
                merchants = data['searchResult'].get('searchMerchants', [])
            elif 'merchantList' in data:
                merchants = data.get('merchantList', [])
            
            # Filter by keyword if provided
            if keyword and keyword.lower() != "food":
                filtered = []
                kw = keyword.lower()
                for m in merchants:
                    # Check name, cuisine, tags
                    brief = m.get('merchantBrief', {})
                    name = brief.get('displayInfo', {}).get('primaryText') or m.get('address', {}).get('name', '[No Name]')
                    cuisines = " ".join(brief.get('cuisine', []))
                    
                    if kw in name.lower() or kw in cuisines.lower():
                        filtered.append(m)
                
                logger.info(f"Filtered {len(merchants)} -> {len(filtered)} results for keyword '{keyword}'")
                merchants = filtered
            
            result = merchants[:limit]
            
            # Cache the result
            if use_cache:
                self._set_cached_result(cache_key, result)
            
            return result
            
        except Exception as e:
            logger.exception(f"API Error: {e}")
            return []

    def get_restaurant_menu(self, restaurant_id, latlng="3.139,101.6869"):
        """
        Fetch menu items for a specific restaurant.
        
        Args:
            restaurant_id: The restaurant ID (e.g., "1-C6D3VKNKTTW3JX")
            latlng: Location coordinates (default: "3.139,101.6869")
            
        Returns:
            Dictionary with restaurant info and menu items
        """
        self._rate_limit()
        
        target_url = f"https://portal.grab.com/foodweb/guest/v2/merchants/{restaurant_id}"
        
        params = {
            "latlng": latlng,
        }
        
        headers = self.headers.copy()
        
        logger.info(f"Fetching menu for restaurant: {restaurant_id}")
        
        try:
            response = requests.get(target_url, headers=headers, params=params, timeout=15)
            logger.info(f"Menu Response Status: {response.status_code}")
            
            if self._is_token_expired(response):
                logger.warning("Token expired, attempting refresh...")
                if self.refresh_tokens():
                    self._rate_limit()
                    headers = self.headers.copy()
                    response = requests.get(target_url, headers=headers, params=params, timeout=15)
                    logger.info(f"Menu Retry Response Status: {response.status_code}")
                else:
                    logger.error("Token refresh failed")
                    return None
            
            if response.status_code != 200:
                logger.error(f"Menu fetch error: {response.status_code} - {response.text[:500]}")
                return None
            
            data = response.json()
            logger.info(f"Menu response keys: {list(data.keys())}")
            if "merchant" in data:
                logger.info(f"Merchant keys: {list(data.get('merchant', {}).keys())}")
                logger.info(f"Categories: {data.get('merchant', {}).get('categories', []).__len__()}")
            
            return self._parse_menu_data(data, restaurant_id)
            
        except Exception as e:
            logger.exception(f"Menu API Error: {e}")
            return None

    def _parse_menu_data(self, data, restaurant_id):
        """Parse the menu response into a structured format"""
        result = {
            "restaurant_id": restaurant_id,
            "name": "",
            "categories": [],
            "items": []
        }
        
        try:
            merchant = data.get("merchant", {})
            result["name"] = merchant.get("name", "Unknown Restaurant")
            
            menu = merchant.get("menu", {})
            categories = menu.get("categories", [])
            for category in categories:
                category_name = category.get("name", "Other")
                items = category.get("items", [])
                
                category_obj = {
                    "name": category_name,
                    "items": []
                }
                
                for item in items:
                    price = item.get("priceInMinorUnit", 0)
                    item_data = {
                        "id": item.get("ID", ""),
                        "name": item.get("name", ""),
                        "description": item.get("description", ""),
                        "price": price / 100 if price else 0,
                        "currency": "MYR",
                        "image": item.get("imgHref", ""),
                        "is_available": item.get("available", True),
                        "modifier_groups": []
                    }
                    
                    for mg in item.get("modifierGroups", []):
                        mg_data = {
                            "id": mg.get("ID", ""),
                            "name": mg.get("name", ""),
                            "selection_min": mg.get("selectionRangeMin", 1),
                            "selection_max": mg.get("selectionRangeMax", 1),
                            "modifiers": []
                        }
                        
                        for mod in mg.get("modifiers", []):
                            mod_price = mod.get("priceInMinorUnit", 0)
                            mod_data = {
                                "id": mod.get("ID", ""),
                                "name": mod.get("name", ""),
                                "price": mod_price / 100 if mod_price else 0,
                                "available": mod.get("available", True)
                            }
                            mg_data["modifiers"].append(mod_data)
                        
                        item_data["modifier_groups"].append(mg_data)
                    
                    category_obj["items"].append(item_data)
                    result["items"].append(item_data)
                
                result["categories"].append(category_obj)
                
        except Exception as e:
            logger.exception(f"Failed to parse menu data: {e}")
        
        return result

if __name__ == "__main__":
    # Test Block
    client = GrabFoodClient()
    
    # Kuala Lumpur Coordinates
    lat = 3.1390
    lng = 101.6869
    
    print("Testing Search Strategy...")
    restaurants = client.search_restaurants(lat, lng, keyword="Murni")
    
    if restaurants:
        print(f"\nSUCCESS! Found {len(restaurants)} restaurants.")
        print(f"First one: {restaurants[0].get('address', {}).get('name')}")
        
        # Save for inspection
        with open("simple_test_result.json", "w") as f:
            json.dump(restaurants, f, indent=2)
            print("Saved results to simple_test_result.json")
    else:
        print("\nFAILED. No restaurants found.")
