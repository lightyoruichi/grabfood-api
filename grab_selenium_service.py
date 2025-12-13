from seleniumwire import webdriver  # Requires: pip install selenium-wire
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager # Requires: pip install webdriver-manager
from selenium.webdriver.chrome.options import Options
import time
import json
import logging
import os
import shutil
import glob

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GrabSeleniumService:
    """
    A service that uses a headless browser to retrieve legitimate authentication headers
    (x-recaptcha-token, cookies, etc.) from GrabFood.
    """
    
    def __init__(self, headless=True):
        self.headless = headless
        self.driver = None

    def _setup_driver(self):
        chrome_options = Options()
        # Essential anti-detection options
        if self.headless:
            chrome_options.add_argument("--headless=new") # New headless mode is more stealthy
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        # spoof user agent - Updated to newer Chrome 131
        user_agent = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
        chrome_options.add_argument(f'user-agent={user_agent}')
        
        # Additional flags to prevent 403s on assets
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--allow-running-insecure-content")
        chrome_options.add_argument("--disable-popup-blocking")
        chrome_options.add_argument("--lang=en-US")
        
        # Environment variables for Railway/Production
        chrome_bin = os.environ.get("CHROME_BIN")
        chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")
        
        # Debugging Railway Environment
        logger.info(f"PATH: {os.environ.get('PATH')}")
        logger.info(f"shutil.which('chromium'): {shutil.which('chromium')}")
        logger.info(f"shutil.which('chromium-browser'): {shutil.which('chromium-browser')}")
        logger.info(f"shutil.which('chromedriver'): {shutil.which('chromedriver')}")
        logger.info(f"shutil.which('chromium-driver'): {shutil.which('chromium-driver')}")

        # Auto-detect system binaries if not explicitly set (Critical for Railway/Nixpacks)
        if not chrome_bin:
            chrome_bin = shutil.which("chromium") or shutil.which("google-chrome") or shutil.which("chromium-browser")
            
            # Fallback: Check /nix/store if not in PATH (Common in Nixpacks)
            if not chrome_bin and os.path.exists("/nix/store"):
                logger.info("Searching /nix/store for chromium...")
                # Look for chromium executable in nix store
                matches = glob.glob("/nix/store/*-chromium-*/bin/chromium")
                if matches:
                    chrome_bin = matches[0]
                    logger.info(f"Found chromium in /nix/store: {chrome_bin}")

        if not chromedriver_path:
            chromedriver_path = shutil.which("chromedriver") or shutil.which("chromium-driver")
            
            # Fallback: Check /nix/store
            if not chromedriver_path and os.path.exists("/nix/store"):
                logger.info("Searching /nix/store for chromedriver...")
                matches = glob.glob("/nix/store/*-chromedriver-*/bin/chromedriver")
                if matches:
                    chromedriver_path = matches[0]
                    logger.info(f"Found chromedriver in /nix/store: {chromedriver_path}")

        if chrome_bin:
            logger.info(f"Using Chrome Binary: {chrome_bin}")
            chrome_options.binary_location = chrome_bin
        else:
             logger.info("Chrome binary not found in PATH or env vars, relying on default.")
        
        # Additional safe options for container environments
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--headless=new") 

        # Wire interceptor options
        seleniumwire_options = {
            'disable_encoding': True,
            'verify_ssl': False,
            'connection_timeout': 10  # faster timeout
        }

        try:
            if chromedriver_path:
                logger.info(f"Using System Chromedriver: {chromedriver_path}")
                service = Service(executable_path=chromedriver_path)
            else:
                logger.info("System Chromedriver not found. Falling back to ChromeDriverManager (may fail in containers).")
                service = Service(ChromeDriverManager().install())
                
            self.driver = webdriver.Chrome(
                service=service, 
                options=chrome_options,
                seleniumwire_options=seleniumwire_options
            )
        except Exception as e:
            logger.error(f"Failed to initialize WebDriver: {e}")
            raise

        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
            """
        })

    def get_auth_context(self, lat, lng, country_code="MY"):
        """
        Navigates to GrabFood, triggers a search, and intercepts the tokens.
        Returns a dictionary of headers and cookies suitable for use with requests.
        """
        if not self.driver:
            self._setup_driver()
        
        auth_context = {}
        
        try:
            # 1. Navigate to the restaurants page to establish session/cookies
            # Go to GrabFood Home - this usually forces the address input to appear
            # 1. Navigate to the restaurants page to establish session/cookies/tokens
            # Trying direct access to restaurants listing to force API calls
            url = "https://food.grab.com/my/en/restaurants" 
            logger.info(f"Navigating to {url}...")
            self.driver.get(url)
            
            # Use WebDriverWait instead of fixed sleep
            from selenium.webdriver.common.by import By
            from selenium.webdriver.common.keys import Keys
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            
            wait = WebDriverWait(self.driver, 15)

            # Updated to match both /foodweb/v2/search and /foodweb/guest/v2/search
            target_api = "v2/search" 
            found_token = False
            
            # Helper to check requests
            def check_requests():
                for request in self.driver.requests:
                    if "portal.grab.com" in request.url:
                        logger.info(f"Inspecting request: {request.url}")
                        # Log all x- headers for debugging
                        x_headers = {k: v for k, v in request.headers.items() if k.lower().startswith('x-')}
                        logger.info(f"Found X-Headers: {json.dumps(x_headers)}")
                        
                        auth_context['headers'] = dict(request.headers)
                        auth_context['cookies'] = self.driver.get_cookies()
                        
                        token_key = next((k for k in request.headers.keys() if 'x-recaptcha-token' in k.lower()), None)
                        if token_key:
                            logger.info(f"Found explicit token in {token_key}!")
                            return True # Found it!
                        else:
                            logger.info("request captured, but no x-recaptcha-token yet.")
                return False

            # Check initial load
            time.sleep(5) # Keep a small buffer for network idle
            if check_requests():
                found_token = True
            
            if not found_token:
                logger.warning("Token not found in initial requests. Attempting to force search...")
                
                # Snapshot for debugging
                self.driver.save_screenshot("debug_initial_load.png")

                try:
                    # Wait for ANY input to be present
                    logger.info("Waiting for input element...")
                    inputs = wait.until(EC.presence_of_all_elements_located((By.TAG_NAME, "input")))
                    logger.info(f"Found {len(inputs)} inputs on the page.")

                    address_input = None
                    for inp in inputs:
                        ph = inp.get_attribute("placeholder")
                        if ph and ("address" in ph.lower() or "location" in ph.lower() or "delivering to" in ph.lower()):
                            address_input = inp
                            break
                    
                    if address_input:
                        logger.info("Found address input. interacting...")
                        # ... (interaction logic) ...
                    
                    # FALLBACK: Check Local Storage / Session Storage / HTML
                    logger.info("Checking LocalStorage for tokens...")
                    try:
                        # Grab often stores tokens in localStorage keys starting with 'grab_' or inside specific keys
                        ls_data = self.driver.execute_script("return window.localStorage;")
                        if ls_data:
                            # iterate and look for token-like things
                            for k, v in ls_data.items():
                                if 'token' in k.lower() or 'auth' in k.lower():
                                    logger.info(f"Potential LS Token in {k}: {v[:50]}...")
                                    
                        # Also check for x-recaptcha-token specifically if stored
                        # Sometimes it's in a global variable or simpler location
                        
                        # Check keys for 'keys'
                        # keys = self.driver.execute_script("return Object.keys(window.localStorage);")
                        
                    except Exception as e:
                        logger.error(f"LS check failed: {e}")

                    # FALLBACK 2: Check __NEXT_DATA__
                    try:
                        next_data = self.driver.execute_script("return window.__NEXT_DATA__;")
                        if next_data:
                            # Recursively search for 'token' or 'recaptcha'
                            s_data = json.dumps(next_data)
                            if 'recaptcha' in s_data.lower():
                                logger.info("Found 'recaptcha' string in __NEXT_DATA__!")
                                # We might need to parse it to find the actual value
                                # This is complex, but let's at least know if it's there
                    except:
                        pass

                        
                        # Type slowly to simulate human
                        try:
                            wait.until(EC.element_to_be_clickable(address_input))
                            address_input.click()
                        except:
                            self.driver.execute_script("arguments[0].click();", address_input)
                        
                        address_input.clear()
                        address_input.send_keys("Kuala Lumpur")
                        time.sleep(1)
                        
                        # Press Enter
                        address_input.send_keys(Keys.ENTER)
                        time.sleep(2)

                    # FALLBACK 3: Try to find Recaptcha Site Key and execute
                    try:
                        logger.info("Attempting to find Recaptcha Site Key...")
                        # Look for script tag with 'render='
                        scripts = self.driver.find_elements(By.TAG_NAME, "script")
                        site_key = None
                        for s in scripts:
                            src = s.get_attribute("src") or ""
                            if "render=" in src and "google.com/recaptcha" in src:
                                # format: ...render=SITE_KEY...
                                import re
                                match = re.search(r'render=([^&]+)', src)
                                if match:
                                    site_key = match.group(1)
                                    logger.info(f"Found Site Key: {site_key}")
                                    break
                        
                        if site_key and site_key != 'explicit':
                            logger.info(f"Executing grecaptcha with key {site_key}...")
                            token = self.driver.execute_async_script("""
                                var done = arguments[0];
                                grecaptcha.ready(function() {
                                    grecaptcha.execute('""" + site_key + """', {action: 'submit'}).then(function(token) {
                                        done(token);
                                    });
                                });
                            """)
                            if token:
                                logger.info(f"Manually generated x-recaptcha-token: {token[:50]}...")
                                auth_context['headers']['x-recaptcha-token'] = token
                                return True
                    except Exception as e:
                        logger.error(f"Manual Recaptcha generation failed: {e}")
                        
                        # Wait for results potentially
                        time.sleep(5)
                    else:
                        logger.warning("No suitable input found.")
                        
                    # Check again
                    if check_requests():
                        found_token = True

                except Exception as ex:
                    logger.error(f"Failed to force search interaction: {ex}")
                    with open("debug_interaction_fail.html", "w", encoding="utf-8") as f:
                        f.write(self.driver.page_source)

            
            if not found_token:
                logger.warning("Still could not find token. Saving final debug screenshot.")
                self.driver.save_screenshot("debug_final_fail.png")
                
        except Exception as e:
            logger.error(f"Error getting auth context: {e}")
            
        finally:
            self.teardown()
            
        return auth_context

    def teardown(self):
        if self.driver:
            self.driver.quit()
            self.driver = None

if __name__ == "__main__":
    service = GrabSeleniumService(headless=True)
    # Test coordinates (KL)
    ctx = service.get_auth_context(4.543089619127414, 101.04479068977173)
    
    if ctx.get('headers'):
        print("\n--- Captured Headers ---")
        token = ctx['headers'].get('x-recaptcha-token')
        if token:
            print(f"x-recaptcha-token: {token[:50]}...")
        else:
            print("x-recaptcha-token: None (using session cookies/headers)")
        
        # Save to a temporary file for the requests script to usage
        with open("grab_auth_context.json", "w") as f:
            # Convert cookie objects to simple dict if needed, request.headers is dict-like
            # Cookies from selenium are list of dicts
            
            # Helper to format cookies for requests
            req_cookies = {c['name']: c['value'] for c in ctx['cookies']}
            
            data = {
                "headers": {k: v for k, v in ctx['headers'].items() if k.lower() not in ['content-length', 'content-encoding']}, # Clean headers
                "cookies": req_cookies
            }
            json.dump(data, f, indent=2)
            print("Saved auth context to grab_auth_context.json")
    else:
        print("Failed to capture auth headers.")
