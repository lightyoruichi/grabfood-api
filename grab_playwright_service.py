from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import time
import json
import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GrabPlaywrightService:
    """
    A service that uses Playwright to retrieve legitimate authentication headers
    (x-recaptcha-token, cookies, etc.) from GrabFood.
    """
    
    def __init__(self, headless=True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def _setup_browser(self):
        """Initialize Playwright browser with anti-detection settings"""
        self.playwright = sync_playwright().start()
        
        # Browser launch options
        launch_options = {
            "headless": self.headless,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1920,1080",
            ]
        }
        
        # Check for custom Chrome binary path (for Railway/Nixpacks)
        chrome_bin = os.environ.get("CHROME_BIN")
        if chrome_bin:
            logger.info(f"Using Chrome Binary: {chrome_bin}")
            launch_options["executable_path"] = chrome_bin
        
        # Launch browser
        self.browser = self.playwright.chromium.launch(**launch_options)
        
        # Create context with anti-detection settings
        self.context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            locale="en-US",
            timezone_id="Asia/Kuala_Lumpur",
            # Disable automation indicators
            java_script_enabled=True,
            accept_downloads=False,
        )
        
        # Add script to hide webdriver property
        self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        self.page = self.context.new_page()
        
        # Set extra HTTP headers
        self.page.set_extra_http_headers({
            "Accept-Language": "en-US,en;q=0.9",
        })

    def get_auth_context(self, lat, lng, country_code="MY"):
        """
        Navigates to GrabFood, triggers a search, and intercepts the tokens.
        Returns a dictionary of headers and cookies suitable for use with requests.
        """
        if not self.page:
            self._setup_browser()
        
        auth_context = {}
        captured_headers = {}
        captured_cookies = {}
        found_token = False
        
        try:
            # Set up request and response interception
            def handle_request(request):
                """Capture requests to portal.grab.com"""
                if "portal.grab.com" in request.url:
                    logger.info(f"Inspecting request: {request.url}")
                    # Get all headers
                    headers = request.headers
                    x_headers = {k: v for k, v in headers.items() if k.lower().startswith('x-')}
                    if x_headers:
                        logger.info(f"Found X-Headers: {json.dumps(x_headers)}")
                    
                    # Store headers
                    for k, v in headers.items():
                        captured_headers[k] = v
                    
                    # Check for recaptcha token
                    token_key = next((k for k in headers.keys() if 'x-recaptcha-token' in k.lower()), None)
                    if token_key:
                        logger.info(f"Found explicit token in {token_key}!")
                        nonlocal found_token
                        found_token = True
            
            def handle_response(response):
                """Capture responses from portal.grab.com"""
                if "portal.grab.com" in response.url:
                    logger.info(f"Inspecting response: {response.url}")
                    # Response headers might also contain useful info
                    headers = response.headers
                    x_headers = {k: v for k, v in headers.items() if k.lower().startswith('x-')}
                    if x_headers:
                        logger.info(f"Found X-Headers in response: {json.dumps(x_headers)}")
            
            self.page.on("request", handle_request)
            self.page.on("response", handle_response)
            
            # 1. Navigate to the restaurants page
            url = "https://food.grab.com/my/en/restaurants"
            logger.info(f"Navigating to {url}...")
            self.page.goto(url, wait_until="networkidle", timeout=30000)
            
            # Wait a bit for initial requests
            time.sleep(5)
            
            # Check if we already captured the token
            if not found_token:
                found_token = any('x-recaptcha-token' in k.lower() for k in captured_headers.keys())
            
            if not found_token:
                logger.warning("Token not found in initial requests. Attempting to force search...")
                
                # Take screenshot for debugging
                self.page.screenshot(path="debug_initial_load.png")
                
                try:
                    # Wait for input elements
                    logger.info("Waiting for input element...")
                    inputs = self.page.query_selector_all("input")
                    logger.info(f"Found {len(inputs)} inputs on the page.")
                    
                    address_input = None
                    for inp in inputs:
                        placeholder = inp.get_attribute("placeholder")
                        if placeholder and ("address" in placeholder.lower() or 
                                          "location" in placeholder.lower() or 
                                          "delivering to" in placeholder.lower()):
                            address_input = inp
                            break
                    
                    if address_input:
                        logger.info("Found address input. Interacting...")
                        try:
                            address_input.click(timeout=5000)
                        except:
                            address_input.evaluate("el => el.click()")
                        
                        address_input.fill("Kuala Lumpur")
                        time.sleep(1)
                        
                        # Press Enter
                        address_input.press("Enter")
                        time.sleep(2)
                    
                    # FALLBACK: Check Local Storage
                    logger.info("Checking LocalStorage for tokens...")
                    try:
                        ls_data = self.page.evaluate("() => { return window.localStorage; }")
                        if ls_data:
                            for k, v in ls_data.items():
                                if 'token' in k.lower() or 'auth' in k.lower():
                                    logger.info(f"Potential LS Token in {k}: {v[:50]}...")
                    except Exception as e:
                        logger.error(f"LS check failed: {e}")
                    
                    # FALLBACK 2: Check __NEXT_DATA__
                    try:
                        next_data = self.page.evaluate("() => { return window.__NEXT_DATA__; }")
                        if next_data:
                            s_data = json.dumps(next_data)
                            if 'recaptcha' in s_data.lower():
                                logger.info("Found 'recaptcha' string in __NEXT_DATA__!")
                    except:
                        pass
                    
                    # FALLBACK 3: Try to find Recaptcha Site Key and execute
                    try:
                        logger.info("Attempting to find Recaptcha Site Key...")
                        site_key = self.page.evaluate("""
                            () => {
                                const scripts = document.querySelectorAll('script');
                                for (let s of scripts) {
                                    const src = s.src || '';
                                    if (src.includes('render=') && src.includes('google.com/recaptcha')) {
                                        const match = src.match(/render=([^&]+)/);
                                        if (match) return match[1];
                                    }
                                }
                                return null;
                            }
                        """)
                        
                        if site_key and site_key != 'explicit':
                            logger.info(f"Executing grecaptcha with key {site_key}...")
                            token = self.page.evaluate(f"""
                                () => {{
                                    return new Promise((resolve) => {{
                                        grecaptcha.ready(function() {{
                                            grecaptcha.execute('{site_key}', {{action: 'submit'}}).then(function(token) {{
                                                resolve(token);
                                            }});
                                        }});
                                    }});
                                }}
                            """)
                            if token:
                                logger.info(f"Manually generated x-recaptcha-token: {token[:50]}...")
                                captured_headers['x-recaptcha-token'] = token
                                found_token = True
                    except Exception as e:
                        logger.error(f"Manual Recaptcha generation failed: {e}")
                    
                    # Wait for results
                    time.sleep(5)
                    
                    # Check again
                    found_token = any('x-recaptcha-token' in k.lower() for k in captured_headers.keys())
                    
                except Exception as ex:
                    logger.error(f"Failed to force search interaction: {ex}")
                    # Save page HTML for debugging
                    with open("debug_interaction_fail.html", "w", encoding="utf-8") as f:
                        f.write(self.page.content())
            
            if not found_token:
                logger.warning("Still could not find token. Saving final debug screenshot.")
                self.page.screenshot(path="debug_final_fail.png")
            
            # Get cookies from context
            cookies = self.context.cookies()
            for cookie in cookies:
                captured_cookies[cookie['name']] = cookie['value']
            
            # Build auth context
            auth_context['headers'] = captured_headers
            auth_context['cookies'] = captured_cookies
                
        except Exception as e:
            logger.error(f"Error getting auth context: {e}")
            
        finally:
            self.teardown()
            
        return auth_context

    def teardown(self):
        """Clean up browser resources"""
        if self.page:
            self.page.close()
            self.page = None
        if self.context:
            self.context.close()
            self.context = None
        if self.browser:
            self.browser.close()
            self.browser = None
        if self.playwright:
            self.playwright.stop()
            self.playwright = None

if __name__ == "__main__":
    service = GrabPlaywrightService(headless=True)
    # Test coordinates (KL)
    ctx = service.get_auth_context(3.1390, 101.6869)
    
    if ctx.get('headers'):
        print("\n--- Captured Headers ---")
        token = ctx['headers'].get('x-recaptcha-token')
        if token:
            print(f"x-recaptcha-token: {token[:50]}...")
        else:
            print("x-recaptcha-token: None (using session cookies/headers)")
        
        # Save to a temporary file for the requests script to use
        with open("grab_auth_context.json", "w") as f:
            # Format cookies for requests
            req_cookies = ctx['cookies']
            
            data = {
                "headers": {k: v for k, v in ctx['headers'].items() 
                           if k.lower() not in ['content-length', 'content-encoding']},
                "cookies": req_cookies
            }
            json.dump(data, f, indent=2)
            print("Saved auth context to grab_auth_context.json")
    else:
        print("Failed to capture auth headers.")

