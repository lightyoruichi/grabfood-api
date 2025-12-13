import requests
import json
import os
import time
from urllib.parse import quote

class GrabFoodClient:
    def __init__(self):
        self.BASE_URL = "https://portal.grab.com/foodweb/v2"
        self.GUEST_URL = "https://portal.grab.com/foodweb/guest/v2"
        self.headers = self._get_headers()

    def _get_headers(self):
        # Default minimal headers
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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
                            
                    print("Loaded Authentication Context from grab_auth_context.json")
            except Exception as e:
                print(f"Failed to load auth context: {e}")
        
        return headers

    def search_restaurants(self, lat, lng, keyword="food", limit=32):
        """
        Search for restaurants using the Guest API (v2/category) and filter locally.
        This bypasses the need for x-recaptcha-token required by v2/search.
        """
        # Endpoint for guest category listing (works without strict auth)
        target_url = "https://portal.grab.com/foodweb/guest/v2/category"
        
        # We use a generic category ID to get a broad list. 
        # ID 7077 is often "Food Delivery" or similar generic category.
        # or we can omit it? Let's try to minimal params first found in previous working calls.
        params = {
            "latlng": f"{lat},{lng}",
            "categoryShortcutID": "7077", # Generic "Food" category
            "offset": "0",
            "pageSize": str(limit * 2), # Fetch more to allow for filtering
            "countryCode": "MY",
        }
        
        print(f"Searching via Guest API: {target_url} with params {params}")
        
        try:
            response = requests.get(target_url, headers=self.headers, params=params, timeout=15)
            print(f"Response Status: {response.status_code}")
            
            if response.status_code != 200:
                print(f"Error response: {response.text}")
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
                    
                    print(f"Checking: {name} | Cuisines: {cuisines}")
                    
                    if kw in name.lower() or kw in cuisines.lower():
                        filtered.append(m)
                
                print(f"Filtered {len(merchants)} -> {len(filtered)} results for keyword '{keyword}'")
                merchants = filtered
                
            return merchants[:limit]
            
        except Exception as e:
            print(f"API Error: {e}")
            return []

if __name__ == "__main__":
    # Test Block
    client = GrabFoodClient()
    
    # Ipoh Coordinates
    lat = 4.543089619127414
    lng = 101.04479068977173
    
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
