import argparse
import re
import json
import asyncio
import os
import socket
import dns.resolver
import logging
import random
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from collections import deque
from playwright.async_api import async_playwright

def find_embedded_domains(html_text):
    """Finds domain lists embedded in JavaScript within a page's HTML."""
    found_domains = set()
    soup = BeautifulSoup(html_text, 'html.parser')
    regex = r'"__map":\s*(\[\["[\w\.-]+",\d+\].*?\])'
    
    for script_tag in soup.find_all("script"):
        if not script_tag.string:
            continue

        matches = re.findall(regex, script_tag.string)
        
        for match in matches:
            try:
                domain_list = json.loads(match)
                for item in domain_list:
                    if isinstance(item, list) and len(item) > 0:
                        found_domains.add(item[0])
            except json.JSONDecodeError:
                continue
    
    if not found_domains:
        return None
        
    return sorted(list(found_domains))

def normalize_url(url):
    """Normalize URL for deduplication by removing common variations."""
    parsed = urlparse(url)
    
    # Normalize domain (remove www)
    domain = parsed.netloc.lower().replace('www.', '')
    
    # Normalize path (remove trailing slash, convert to lowercase)
    path = parsed.path.rstrip('/').lower()
    
    # Parse and normalize query parameters
    query_params = {}
    if parsed.query:
        from urllib.parse import parse_qs
        params = parse_qs(parsed.query)
        # Keep only the first value for each parameter and sort
        for key, values in params.items():
            query_params[key.lower()] = values[0].lower() if values else ''
    
    # Create normalized query string
    if query_params:
        sorted_params = sorted(query_params.items())
        normalized_query = '&'.join(f"{k}={v}" for k, v in sorted_params)
    else:
        normalized_query = ''
    
    # Reconstruct normalized URL
    normalized = f"{parsed.scheme}://{domain}{path}"
    if normalized_query:
        normalized += f"?{normalized_query}"
    
    return normalized

def is_similar_url(url1, url2, similarity_threshold=0.8):
    """Check if two URLs are very similar (e.g., just different query params or regional variants)."""
    parsed1 = urlparse(url1)
    parsed2 = urlparse(url2)
    
    # Different domains are not similar
    if parsed1.netloc.lower() != parsed2.netloc.lower():
        return False
    
    path1 = parsed1.path.rstrip('/').lower()
    path2 = parsed2.path.rstrip('/').lower()
    
    # Check for regional/language variants (e.g., /en-us/page vs /fr-fr/page)
    import re
    lang_pattern = r'^/[a-z]{2}-[a-z]{2}(/.*)?$'
    
    if re.match(lang_pattern, path1) and re.match(lang_pattern, path2):
        # Extract the path after language code
        path1_without_lang = re.sub(r'^/[a-z]{2}-[a-z]{2}', '', path1)
        path2_without_lang = re.sub(r'^/[a-z]{2}-[a-z]{2}', '', path2)
        if path1_without_lang == path2_without_lang:
            return True
    
    # Check for very similar paths (character similarity)
    if path1 and path2:
        # Simple character-based similarity
        max_len = max(len(path1), len(path2))
        if max_len == 0:
            return True
        
        # Count matching characters in same positions
        matches = sum(1 for i in range(min(len(path1), len(path2))) if path1[i] == path2[i])
        similarity = matches / max_len
        
        if similarity >= similarity_threshold:
            return True
    
    # Check for similar query parameters (different feed types, view types, etc.)
    if path1 == path2:
        from urllib.parse import parse_qs
        params1 = parse_qs(parsed1.query) if parsed1.query else {}
        params2 = parse_qs(parsed2.query) if parsed2.query else {}
        
        # If paths are identical and only query params differ, check for feed/view variations
        common_variation_params = ['feed', 'view', 'feedviewtype', 'sort', 'time', 'layout']
        params1_keys = set(k.lower() for k in params1.keys())
        params2_keys = set(k.lower() for k in params2.keys())
        
        # If the only differences are in common variation parameters, consider similar
        if any(param in params1_keys or param in params2_keys for param in common_variation_params):
            # Remove variation parameters and compare
            filtered_params1 = {k: v for k, v in params1.items() if k.lower() not in common_variation_params}
            filtered_params2 = {k: v for k, v in params2.items() if k.lower() not in common_variation_params}
            if filtered_params1 == filtered_params2:
                return True
    
    return False

def should_skip_url(url, crawled_urls, urls_to_crawl):
    """Determine if a URL should be skipped due to similarity with already processed URLs."""
    # Check against already crawled URLs
    for crawled_url in crawled_urls:
        if is_similar_url(url, crawled_url):
            return True, f"similar to already crawled: {crawled_url}"
    
    # Check against URLs in queue
    for queued_url in urls_to_crawl:
        if is_similar_url(url, queued_url):
            return True, f"similar to queued: {queued_url}"
    
    return False, None

async def crawl_domains(start_url, max_pages, page):
    """Crawls a website by following links to find domains."""
    urls_to_crawl = deque([start_url])
    crawled_urls = set()
    found_subdomains = set()
    found_external_domains = set()
    base_domain = urlparse(start_url).netloc.replace("www.", "")
    ignored_extensions = ['.png', '.jpg', '.css', '.js', '.ico', '.svg', '.xml', '.pdf', '.zip']
    skipped_similar = 0

    while urls_to_crawl and len(crawled_urls) < max_pages:
        url = urls_to_crawl.popleft()
        if url in crawled_urls: 
            continue

        print(f"Crawling page {len(crawled_urls) + 1}/{max_pages}: {url}")
        crawled_urls.add(url)
        
        try:
            # Skip problematic URLs that typically require authentication or cause issues
            problematic_paths = ['/submit', '/notifications', '/settings', '/account', '/login', '/register']
            if any(path in url.lower() for path in problematic_paths):
                print(f"  -> Skipping authentication-required page")
                continue
            
            # Add delay to prevent rapid navigation issues
            await asyncio.sleep(2)
            
            # Navigate to the page with better error handling
            try:
                response = await page.goto(url, wait_until='domcontentloaded', timeout=15000)
            except Exception as nav_error:
                if "net::ERR_NAME_NOT_RESOLVED" in str(nav_error):
                    print(f"  -> DNS resolution failed (subdomain may not exist)")
                elif "interrupted by another navigation" in str(nav_error):
                    print(f"  -> Navigation interrupted by redirect")
                    # Try to get the current page after redirect
                    try:
                        await page.wait_for_load_state('domcontentloaded', timeout=5000)
                        current_url = page.url
                        if current_url != url and not current_url.startswith('chrome-error://'):
                            print(f"    Redirected to: {current_url}")
                            # Extract domains from the redirected page
                            html_content = await page.content()
                            soup = BeautifulSoup(html_content, 'html.parser')
                        else:
                            print(f"    Redirect failed or led to error page")
                            continue
                    except Exception:
                        continue
                else:
                    print(f"  -> Navigation failed: {nav_error}")
                    continue
            else:
                if not response or response.status >= 400:
                    print(f"  -> Failed to load page (status: {response.status if response else 'unknown'})")
                    continue
                    
                # Check content type
                content_type = response.headers.get('content-type', '')
                if 'text/html' not in content_type:
                    print(f"  -> Skipping non-HTML content")
                    continue
                    
                # Wait a bit for any dynamic content to load
                await asyncio.sleep(2)
                
                # Get the page content after JavaScript execution
                html_content = await page.content()
                soup = BeautifulSoup(html_content, 'html.parser')
            
        except Exception as e:
            print(f"  -> Unexpected error: {e}")
            continue

        # Extract links from various elements
        for tag in soup.find_all(['a', 'link'], href=True) + soup.find_all(['script', 'img'], src=True):
            link = tag.get('href') or tag.get('src')
            if not link or link.startswith(('mailto:', 'tel:', 'javascript:', '#')): 
                continue

            absolute_link = urljoin(url, link)
            domain = urlparse(absolute_link).netloc

            if not domain: 
                continue

            if domain.endswith(base_domain):
                if domain != base_domain: 
                    found_subdomains.add(domain)
                if (absolute_link not in crawled_urls and
                    absolute_link not in urls_to_crawl and
                    not any(absolute_link.lower().endswith(ext) for ext in ignored_extensions)):
                    
                    # Check if this URL is too similar to already processed URLs
                    should_skip, reason = should_skip_url(absolute_link, crawled_urls, urls_to_crawl)
                    if should_skip:
                        skipped_similar += 1
                        # Optionally log the first few skips for debugging
                        if skipped_similar <= 3:
                            print(f"  -> Skipping similar URL: {absolute_link}")
                            print(f"     Reason: {reason}")
                    else:
                        urls_to_crawl.append(absolute_link)
            else:
                found_external_domains.add(domain)
    
    if skipped_similar > 0:
        print(f"\n📊 Crawling efficiency: Skipped {skipped_similar} similar/duplicate URLs")
                
    return sorted(list(found_subdomains)), sorted(list(found_external_domains))

def extract_parent_domains(domains):
    """Extract parent domains from a list of domains."""
    parent_domains = set()
    
    for domain in domains:
        parts = domain.split('.')
        # For domains with 3+ parts, extract parent domains
        # e.g., "styles.redditmedia.com" -> "redditmedia.com"
        if len(parts) >= 3:
            for i in range(1, len(parts) - 1):
                parent_domain = '.'.join(parts[i:])
                # Only add if it's not the original domain and has at least 2 parts
                if parent_domain != domain and len(parent_domain.split('.')) >= 2:
                    parent_domains.add(parent_domain)
    
    return sorted(list(parent_domains))

def categorize_domains(base_domain, subdomains, external_domains):
    """Intelligently categorize domains based on their relationship to the base domain."""
    # Extract the main domain name (e.g., "reddit" from "reddit.com")
    base_name = base_domain.split('.')[0].lower()
    
    # Common variations to look for
    variations = [
        base_name,
        base_name + 'media',
        base_name + 'static', 
        base_name + 'cdn',
        base_name + 'inc',
        base_name + 'blog',
        base_name + 'help',
        base_name + 'api',
        base_name + 'assets'
    ]
    
    # Also look for shortened versions (e.g., "redd" for "reddit")
    if len(base_name) > 4:
        short_name = base_name[:4]
        variations.append(short_name)
    
    # First, categorize external domains into related vs third-party
    related_domains = []
    third_party_domains = []
    
    for domain in external_domains:
        domain_lower = domain.lower()
        is_related = False
        
        # Check if domain contains any variation of the base name
        for variation in variations:
            if variation in domain_lower:
                is_related = True
                break
        
        # Special case: check for abbreviated versions in TLD (like redd.it)
        if not is_related and len(base_name) > 4:
            domain_parts = domain_lower.split('.')
            if len(domain_parts) >= 2:
                # Check if abbreviated name appears before TLD
                second_level = domain_parts[-2]
                if base_name[:4] in second_level or second_level in base_name:
                    is_related = True
        
        if is_related:
            related_domains.append(domain)
        else:
            third_party_domains.append(domain)
    
    # Now extract parent domains ONLY from subdomains and related domains
    # (not from third-party domains, as those would create false positives)
    organization_domains = subdomains + related_domains
    parent_domains = extract_parent_domains(organization_domains)
    
    # Filter parent domains to only include those that are organization-related
    filtered_parent_domains = []
    for parent in parent_domains:
        parent_lower = parent.lower()
        is_org_related = False
        
        # Check if parent domain contains organization name variations
        for variation in variations:
            if variation in parent_lower:
                is_org_related = True
                break
        
        # Special case for abbreviated versions
        if not is_org_related and len(base_name) > 4:
            parent_parts = parent_lower.split('.')
            if len(parent_parts) >= 2:
                second_level = parent_parts[-2]
                if base_name[:4] in second_level or second_level in base_name:
                    is_org_related = True
        
        if is_org_related:
            filtered_parent_domains.append(parent)
    
    # Add filtered parent domains to related domains if not already present
    for parent in filtered_parent_domains:
        if parent not in related_domains:
            related_domains.append(parent)
    
    return sorted(related_domains), sorted(third_party_domains), sorted(filtered_parent_domains)

async def validate_domain_dns(domain):
    """Validate if a domain exists via DNS lookup."""
    try:
        # Try both A and AAAA records
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: dns.resolver.resolve(domain, 'A')
        )
        return True
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        try:
            # Try AAAA record (IPv6)
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: dns.resolver.resolve(domain, 'AAAA')
            )
            return True
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return False
    except Exception:
        # For any other DNS errors, assume domain is valid to be safe
        return True

def load_existing_domains(output_file):
    """Load existing domains from output file."""
    existing_domains = set()
    if not os.path.exists(output_file):
        return existing_domains
    
    try:
        with open(output_file, 'r') as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if line and not line.startswith('#'):
                    # Handle both simple domain lists and markdown format
                    if line.startswith('- '):
                        domain = line[2:].strip()
                    elif '.' in line and not line.startswith('##'):
                        domain = line.strip()
                    else:
                        continue
                    
                    if domain:
                        existing_domains.add(domain)
        
        print(f"📋 Loaded {len(existing_domains)} existing domains from {output_file}")
        return existing_domains
        
    except Exception as e:
        print(f"⚠️  Warning: Could not load existing domains from {output_file}: {e}")
        return set()

async def validate_existing_domains(existing_domains):
    """Validate existing domains via DNS and remove dead ones."""
    if not existing_domains:
        return set(), set()
    
    print(f"🔍 Validating {len(existing_domains)} existing domains via DNS...")
    
    # Create semaphore to limit concurrent DNS queries
    dns_semaphore = asyncio.Semaphore(20)  # Limit to 20 concurrent DNS queries
    
    async def check_domain(domain):
        async with dns_semaphore:
            try:
                is_valid = await validate_domain_dns(domain)
                return domain, is_valid
            except Exception as e:
                print(f"   ⚠️  DNS check failed for {domain}: {e}")
                return domain, True  # Assume valid if check fails
    
    # Create tasks for all domain validations
    tasks = [check_domain(domain) for domain in existing_domains]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    valid_domains = set()
    dead_domains = set()
    
    for result in results:
        if isinstance(result, tuple):
            domain, is_valid = result
            if is_valid:
                valid_domains.add(domain)
            else:
                dead_domains.add(domain)
                print(f"   ❌ Dead domain removed: {domain}")
        else:
            print(f"   ⚠️  DNS validation failed: {result}")
    
    if dead_domains:
        print(f"🧹 Removed {len(dead_domains)} dead domains")
    print(f"✅ {len(valid_domains)} domains validated as active")
    
    return valid_domains, dead_domains

def should_skip_url_based_on_existing_domains(url, existing_domains):
    """Determine if a URL should be skipped because we already know its domains."""
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.replace('www.', '')
    
    # If the main domain is already known, we likely have most of its subdomains
    if domain in existing_domains:
        return True, f"main domain {domain} already in existing domains"
    
    # Check if this is a subdomain of an already known domain
    for existing_domain in existing_domains:
        if domain.endswith('.' + existing_domain):
            return True, f"subdomain of known domain {existing_domain}"
    
    return False, None

async def update_output_file_incrementally(output_file, new_domains, existing_domains, dead_domains, args, input_urls=None):
    """Update output file by removing dead domains and adding new ones."""
    if not output_file:
        return
    
    # Calculate domains to write
    domains_to_write = (existing_domains - dead_domains) | new_domains
    total_new = len(new_domains)
    total_removed = len(dead_domains)
    
    # Get current timestamp
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    
    try:
        with open(output_file, 'w') as f:
            if args.fqdn_list:
                # Output consolidated FQDN list format with comprehensive headers
                f.write(f"# Organization FQDN List\n")
                f.write(f"# Generated by Domain Crawler - excludes third-party domains\n")
                f.write(f"# Last updated: {current_time}\n")
                f.write(f"#\n")
                f.write(f"# Input Sources:\n")
                if input_urls:
                    for i, url in enumerate(input_urls, 1):
                        f.write(f"#   {i}. {url}\n")
                else:
                    f.write(f"#   1. {args.start_url if args.start_url else 'Unknown'}\n")
                f.write(f"#\n")
                f.write(f"# Statistics:\n")
                f.write(f"#   Total organization domains: {len(domains_to_write)}\n")
                f.write(f"#   New domains added this run: {total_new}\n")
                f.write(f"#   Dead domains removed this run: {total_removed}\n")
                f.write(f"#   Concurrency level: {args.concurrency}\n")
                f.write(f"#   Max pages per site: {args.pages}\n")
                f.write(f"#\n")
                f.write(f"# Script Configuration:\n")
                f.write(f"#   Headless mode: {args.headless}\n")
                f.write(f"#   Cookie persistence: {not args.no_persist_cookies}\n")
                f.write(f"#   IPv6 support: {args.ipv6}\n")
                if args.url_file:
                    f.write(f"#   URL file: {args.url_file}\n")
                if args.cookies:
                    f.write(f"#   Cookie file: {args.cookies}\n")
                if args.log_file:
                    f.write(f"#   Log file: {args.log_file}\n")
                f.write(f"#\n")
                f.write(f"# Format: One domain per line (FQDN format)\n")
                f.write(f"# ================================================\n\n")
                
                for domain in sorted(domains_to_write):
                    f.write(f"{domain}\n")
            else:
                # Output detailed analysis format with comprehensive headers
                f.write(f"# Domain Analysis Report\n")
                f.write(f"# Generated by Domain Crawler\n")
                f.write(f"# Last updated: {current_time}\n")
                f.write(f"#\n")
                f.write(f"# Input Sources:\n")
                if input_urls:
                    for i, url in enumerate(input_urls, 1):
                        f.write(f"#   {i}. {url}\n")
                else:
                    f.write(f"#   1. {args.start_url if args.start_url else 'Unknown'}\n")
                f.write(f"#\n")
                f.write(f"# Script Configuration:\n")
                f.write(f"#   Concurrency level: {args.concurrency}\n")
                f.write(f"#   Max pages per site: {args.pages}\n")
                f.write(f"#   Headless mode: {args.headless}\n")
                f.write(f"#   Cookie persistence: {not args.no_persist_cookies}\n")
                f.write(f"# ================================================\n\n")
                
                f.write("## Summary\n")
                f.write(f"- Last updated: {current_time}\n")
                f.write(f"- Total organization domains: {len(domains_to_write)}\n")
                f.write(f"- New domains added this run: {total_new}\n")
                f.write(f"- Dead domains removed this run: {total_removed}\n\n")
                
                f.write("## Organization FQDN List\n")
                for domain in sorted(domains_to_write):
                    f.write(f"{domain}\n")
        
        # Log the update
        logging.info(f"Updated output file: {output_file}")
        logging.info(f"Added {total_new} new domains, removed {total_removed} dead domains")
        logging.info(f"Total domains in file: {len(domains_to_write)}")
        
        print(f"\n📄 Updated {output_file}:")
        if total_new > 0:
            print(f"   ✅ Added {total_new} new domains")
        if total_removed > 0:
            print(f"   🗑️  Removed {total_removed} dead domains")
        print(f"   📊 Total domains: {len(domains_to_write)}")
        print(f"   🕒 Last updated: {current_time}")
        
    except Exception as e:
        error_msg = f"Error updating output file: {e}"
        logging.error(error_msg)
        print(f"❌ {error_msg}")

async def process_single_url(url, url_index, total_urls, args, semaphore):
    """Process a single URL and return its results."""
    async with semaphore:  # Limit concurrent browser instances
        # Ensure URL has protocol
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
            print(f"[{url_index + 1}/{total_urls}] Added https:// protocol: {url}")
        
        print(f"\n{'='*60}")
        print(f"🔄 Processing URL {url_index + 1}/{total_urls}: {url}")
        print(f"{'='*60}")
        
        # Set up persistent storage for this URL
        storage_state_file = None
        if not args.no_persist_cookies:
            storage_dir = os.path.join(os.getcwd(), '.browser_data')
            os.makedirs(storage_dir, exist_ok=True)
            
            domain = urlparse(url).netloc.replace('www.', '')
            storage_state_file = os.path.join(storage_dir, f'{domain}_storage.json')
            
            if args.clear_cookies and os.path.exists(storage_state_file):
                os.remove(storage_state_file)
                print(f"🧹 Cleared existing browser data for {domain}")
            
            if os.path.exists(storage_state_file):
                print(f"🍪 Loading existing browser data from {storage_state_file}")
            else:
                print(f"🍪 Creating new browser data file: {storage_state_file}")
        
        # Launch browser for this URL
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=args.headless,
                args=[
                    '--no-sandbox',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-extensions',
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor',
                    '--no-first-run',
                    '--disable-infobars',
                    '--disable-notifications'
                ]
            )
            
            try:
                # Create context with storage state
                context_options = {
                    'user_agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'viewport': {'width': 1366, 'height': 768},
                    'ignore_https_errors': True,
                }
                
                if storage_state_file and os.path.exists(storage_state_file):
                    context_options['storage_state'] = storage_state_file
                
                context = await browser.new_context(**context_options)
                page = await context.new_page()
                
                # Import cookies if provided (for first URL only)
                if args.cookies and url_index == 0:
                    try:
                        print(f"🍪 Loading cookies from {args.cookies}...")
                        with open(args.cookies, 'r') as f:
                            cookies_data = json.load(f)
                        
                        playwright_cookies = []
                        for cookie in cookies_data:
                            playwright_cookie = {
                                'name': cookie['name'],
                                'value': cookie['value'],
                                'domain': cookie['domain'],
                                'path': cookie.get('path', '/'),
                                'secure': cookie.get('secure', False),
                                'httpOnly': cookie.get('httpOnly', False),
                            }
                            
                            if cookie.get('expirationDate') and not cookie.get('session', False):
                                playwright_cookie['expires'] = int(cookie['expirationDate'])
                            
                            same_site = cookie.get('sameSite')
                            if same_site:
                                if same_site == 'no_restriction':
                                    playwright_cookie['sameSite'] = 'None'
                                elif same_site in ['strict', 'lax']:
                                    playwright_cookie['sameSite'] = same_site.capitalize()
                            
                            playwright_cookies.append(playwright_cookie)
                        
                        await context.add_cookies(playwright_cookies)
                        print(f"   ✅ Successfully imported {len(playwright_cookies)} cookies")
                        
                        if storage_state_file:
                            try:
                                await context.storage_state(path=storage_state_file)
                                print(f"   💾 Imported cookies saved to persistent storage: {storage_state_file}")
                            except Exception as e:
                                print(f"   ⚠️  Warning: Could not save imported cookies: {e}")
                                
                    except FileNotFoundError:
                        print(f"   ❌ Cookie file not found: {args.cookies}")
                        return None
                    except json.JSONDecodeError as e:
                        print(f"   ❌ Invalid JSON in cookie file: {e}")
                        return None
                    except Exception as e:
                        print(f"   ❌ Error importing cookies: {e}")
                        return None
                
                try:
                    print("1. Loading initial page...")
                    print(f"   Loading {url}...")
                    response = await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    print(f"   -> Status: {response.status if response else 'unknown'}")
                    
                    if not response or response.status >= 400:
                        print(f"   -> Failed to load {url}")
                        return None
                    
                    # Check for embedded config
                    html_content = await page.content()
                    embedded_domains = find_embedded_domains(html_content)
                    
                    if embedded_domains:
                        print("✅ Success! Found embedded domain config.")
                        subdomains = embedded_domains
                        external_domains = []
                    else:
                        print("2. No config found. Falling back to crawler...")
                        subdomains, external_domains = await crawl_domains(url, args.pages, page)
                    
                    # Get base domain for categorization
                    base_domain = urlparse(url).netloc.replace('www.', '')
                    
                    # Categorize domains for this URL
                    if external_domains:
                        related_domains, third_party_domains, parent_domains = categorize_domains(base_domain, subdomains, external_domains)
                    else:
                        related_domains, third_party_domains, parent_domains = [], [], []
                    
                    # Create consolidated FQDN list for this URL
                    url_consolidated_fqdns = set(subdomains + parent_domains + related_domains + [base_domain])
                    
                    print(f"\n📊 Results for {url}:")
                    print(f"  • Subdomains: {len(subdomains)}")
                    print(f"  • Related domains: {len(related_domains)}")
                    print(f"  • Organization FQDNs: {len(url_consolidated_fqdns)}")
                    
                    return {
                        'url': url,
                        'subdomains': subdomains,
                        'external_domains': external_domains,
                        'related_domains': related_domains,
                        'parent_domains': parent_domains,
                        'consolidated_fqdns': url_consolidated_fqdns
                    }
                    
                except Exception as e:
                    print(f"❌ Error processing {url}: {e}")
                    return None
                finally:
                    # Save storage state
                    if storage_state_file:
                        try:
                            await context.storage_state(path=storage_state_file)
                            print(f"🍪 Browser data saved to {storage_state_file}")
                        except Exception as e:
                            print(f"⚠️  Warning: Could not save browser data: {e}")
                            
            finally:
                await browser.close()

async def main():
    parser = argparse.ArgumentParser(description="Finds domains using config extraction with a crawler fallback.")
    parser.add_argument("start_url", nargs='?', help="The URL to start from (e.g., https://www.reddit.com). Optional if --url-file is provided.")
    parser.add_argument("-p", "--pages", type=int, default=10, help="Max pages to crawl if no config is found.")
    parser.add_argument("-o", "--output", help="Optional file to save the results.")
    parser.add_argument("--headless", action="store_true", default=True, help="Run browser in headless mode (default).")
    parser.add_argument("--no-headless", action="store_false", dest="headless", help="Run browser with UI visible.")
    parser.add_argument("--ipv6", action="store_true", help="Alternate between IPv4 and IPv6 for retry attempts.")
    parser.add_argument("--no-persist-cookies", action="store_true", help="Disable storing cookies and browser data between sessions.")
    parser.add_argument("--clear-cookies", action="store_true", help="Clear stored cookies and browser data before starting.")
    parser.add_argument("--manual-login", action="store_true", help="Pause after loading page to allow manual login before crawling.")
    parser.add_argument("--cookies", help="Path to JSON file containing cookies to import (exported from browser extensions).")
    parser.add_argument("--fqdn-list", action="store_true", help="Output a consolidated FQDN list of all organization-related domains (excludes third-party).")
    parser.add_argument("--url-file", help="Path to file containing URLs to crawl (one per line). If specified, start_url is ignored.")
    parser.add_argument("--concurrency", type=int, default=3, help="Number of concurrent browser instances for batch processing (default: 3, max: 10).")
    parser.add_argument("--log-file", help="Path to log file for unattended runs. If not specified, logs to console only.")
    args = parser.parse_args()
    
    # Setup logging
    log_level = logging.INFO
    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    
    # Configure logging handlers
    handlers = []
    
    # Always add console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(log_format))
    handlers.append(console_handler)
    
    # Add file handler if log file specified
    if args.log_file:
        try:
            # Create log directory if it doesn't exist
            log_dir = os.path.dirname(args.log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir)
            
            file_handler = logging.FileHandler(args.log_file)
            file_handler.setLevel(log_level)
            file_handler.setFormatter(logging.Formatter(log_format))
            handlers.append(file_handler)
            print(f"📝 Logging to file: {args.log_file}")
        except Exception as e:
            print(f"⚠️  Warning: Could not setup file logging: {e}")
    
    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=handlers,
        force=True
    )
    
    # Log script start
    logging.info("Domain crawler started")
    logging.info(f"Arguments: {vars(args)}")
    
    # Validate concurrency limit
    if args.concurrency > 10:
        print("⚠️  Concurrency limited to maximum of 10 to prevent resource exhaustion")
        args.concurrency = 10
    elif args.concurrency < 1:
        args.concurrency = 1
    
    # Validate that either start_url or url_file is provided
    if not args.start_url and not args.url_file:
        parser.error("Either start_url or --url-file must be provided")
    
    if args.url_file and args.start_url:
        print(f"⚠️  Both start_url and --url-file provided. Using --url-file and ignoring start_url.")
    
    # Determine URLs to process
    urls_to_process = []
    if args.url_file:
        try:
            with open(args.url_file, 'r') as f:
                urls_to_process = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
            print(f"📁 Batch processing {len(urls_to_process)} URLs from {args.url_file}")
            if len(urls_to_process) == 0:
                print(f"❌ No valid URLs found in {args.url_file}")
                return
        except FileNotFoundError:
            print(f"❌ URL file not found: {args.url_file}")
            return
        except Exception as e:
            print(f"❌ Error reading URL file: {e}")
            return
    else:
        urls_to_process = [args.start_url]
        print(f"🔎 Analyzing {args.start_url} using Playwright with Chrome browser...")
    
    # Load existing domains from output file if it exists
    existing_domains = set()
    dead_domains = set()
    if args.output:
        existing_domains = load_existing_domains(args.output)
        if existing_domains:
            # Validate existing domains via DNS
            existing_domains, dead_domains = await validate_existing_domains(existing_domains)

    # Filter URLs to process based on existing domains
    filtered_urls = []
    skipped_urls = []
    
    for url in urls_to_process:
        should_skip, reason = should_skip_url_based_on_existing_domains(url, existing_domains)
        if should_skip:
            skipped_urls.append((url, reason))
            print(f"⏭️  Skipping {url}: {reason}")
        else:
            filtered_urls.append(url)
    
    if skipped_urls:
        print(f"📊 Efficiency: Skipped {len(skipped_urls)} URLs with known domains")
    
    # Update URLs to process with filtered list
    urls_to_process = filtered_urls
    
    if not urls_to_process:
        print("✅ All URLs skipped - domains already known and validated")
        if args.output and dead_domains:
            # Still update output file to remove dead domains
            await update_output_file_incrementally(args.output, set(), existing_domains, dead_domains, args)
        return

    # Randomize URL processing order to avoid detection patterns
    if len(urls_to_process) > 1:
        original_order = urls_to_process.copy()
        random.shuffle(urls_to_process)
        print(f"🎲 Randomized processing order for {len(urls_to_process)} URLs to avoid detection patterns")
        logging.info(f"Original URL order: {original_order}")
        logging.info(f"Randomized URL order: {urls_to_process}")

    # Combined results for all URLs
    all_subdomains = set()
    all_external_domains = set()
    all_consolidated_fqdns = set()

    # Determine if we should use concurrent processing
    if len(urls_to_process) > 1 and args.concurrency > 1:
        print(f"🚀 Concurrent processing enabled: {args.concurrency} browser instances")
        
        # Create semaphore to limit concurrent browser instances
        semaphore = asyncio.Semaphore(args.concurrency)
        
        # Create tasks for concurrent processing
        tasks = []
        for url_index, url in enumerate(urls_to_process):
            task = process_single_url(url, url_index, len(urls_to_process), args, semaphore)
            tasks.append(task)
        
        # Process all URLs concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Aggregate results from all successful tasks
        successful_results = 0
        for result in results:
            if isinstance(result, dict) and result is not None:
                all_subdomains.update(result['subdomains'])
                all_external_domains.update(result['external_domains'])
                all_consolidated_fqdns.update(result['consolidated_fqdns'])
                successful_results += 1
            elif isinstance(result, Exception):
                print(f"❌ Task failed with exception: {result}")
        
        print(f"\n🏁 Concurrent processing completed: {successful_results}/{len(urls_to_process)} URLs processed successfully")
        
    else:
        # Single URL or sequential processing
        if len(urls_to_process) == 1:
            print(f"🔎 Analyzing {urls_to_process[0]} using Playwright with Chrome browser...")
        else:
            print(f"📋 Sequential processing (concurrency disabled or set to 1)")
        
        semaphore = asyncio.Semaphore(1)  # Sequential processing
        
        for url_index, url in enumerate(urls_to_process):
            result = await process_single_url(url, url_index, len(urls_to_process), args, semaphore)
            if result:
                all_subdomains.update(result['subdomains'])
                all_external_domains.update(result['external_domains'])
                all_consolidated_fqdns.update(result['consolidated_fqdns'])

    # Final aggregated results
    if len(urls_to_process) > 1:
        print(f"\n{'='*60}")
        print("📊 AGGREGATED RESULTS FROM ALL URLs")
        print(f"{'='*60}")
    
    # Convert sets back to lists and categorize aggregated results
    final_subdomains = sorted(list(all_subdomains))
    final_external_domains = sorted(list(all_external_domains))
    final_consolidated_fqdns = sorted(list(all_consolidated_fqdns))
    
    # Validate newly discovered domains via DNS before adding to output
    if final_consolidated_fqdns:
        print(f"\n🔍 Validating {len(final_consolidated_fqdns)} discovered domains via DNS...")
        
        # Create semaphore to limit concurrent DNS queries
        dns_semaphore = asyncio.Semaphore(20)  # Limit to 20 concurrent DNS queries
        
        async def check_discovered_domain(domain):
            async with dns_semaphore:
                try:
                    is_valid = await validate_domain_dns(domain)
                    return domain, is_valid
                except Exception as e:
                    logging.warning(f"DNS check failed for {domain}: {e}")
                    print(f"   ⚠️  DNS check failed for {domain}: {e}")
                    return domain, False  # If DNS check fails, exclude domain to be safe
        
        # Create tasks for all domain validations
        tasks = [check_discovered_domain(domain) for domain in final_consolidated_fqdns]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        valid_discovered_domains = []
        invalid_discovered_domains = []
        
        for result in results:
            if isinstance(result, tuple):
                domain, is_valid = result
                if is_valid:
                    valid_discovered_domains.append(domain)
                else:
                    invalid_discovered_domains.append(domain)
                    print(f"   ❌ Invalid domain excluded: {domain}")
                    logging.info(f"Excluded invalid domain: {domain}")
            else:
                logging.error(f"DNS validation failed: {result}")
                print(f"   ⚠️  DNS validation failed: {result}")
        
        # Update the final lists to only include valid domains
        final_consolidated_fqdns = sorted(valid_discovered_domains)
        
        # Also filter the individual lists
        valid_domains_set = set(valid_discovered_domains)
        final_subdomains = [d for d in final_subdomains if d in valid_domains_set]
        final_external_domains = [d for d in final_external_domains if d in valid_domains_set]
        
        if invalid_discovered_domains:
            print(f"🧹 Excluded {len(invalid_discovered_domains)} domains that don't resolve in DNS")
            logging.info(f"Excluded {len(invalid_discovered_domains)} invalid domains")
        
        print(f"✅ {len(valid_discovered_domains)} domains validated and ready for output")
        logging.info(f"Validated {len(valid_discovered_domains)} discovered domains")
    
    # For display purposes, use the first URL's base domain or create a generic base
    if final_consolidated_fqdns:
        # Try to find a common base domain pattern
        display_base = "organization"
        if urls_to_process:
            first_url_domain = urlparse(urls_to_process[0] if not urls_to_process[0].startswith('http') else urls_to_process[0]).netloc.replace('www.', '')
            if first_url_domain:
                display_base = first_url_domain.split('.')[0]
    else:
        display_base = "organization"
    
    print(f"\n✅ All Discovered Subdomains:")
    if final_subdomains:
        for domain in final_subdomains: 
            print(f"  - {domain}")
    else:
        print("  None found.")
    
    print(f"\n🔗 All Related Organization Domains:")
    if final_external_domains:
        for domain in final_external_domains: 
            print(f"  - {domain}")
    else:
        print("  None found.")
    
    # Summary statistics
    print(f"\n📈 Final Summary:")
    print(f"  • Total URLs processed: {len(urls_to_process)}")
    print(f"  • Total unique subdomains: {len(final_subdomains)}")
    print(f"  • Total unique external domains: {len(final_external_domains)}")
    print(f"  • Total organization FQDNs: {len(final_consolidated_fqdns)}")
    
    # Output consolidated FQDN list if requested
    if args.fqdn_list:
        print(f"\n🎯 CONSOLIDATED ORGANIZATION FQDN LIST")
        print("="*50)
        print("# All domains associated with the target organizations")
        print("# (Excludes third-party domains)")
        for domain in final_consolidated_fqdns:
            print(domain)
    
    # Update output file incrementally if specified
    if args.output:
        # Calculate new domains discovered in this run
        new_domains = set(final_consolidated_fqdns) - existing_domains
        # Get original URL list for headers
        original_urls = []
        if args.url_file:
            try:
                with open(args.url_file, 'r') as f:
                    original_urls = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
            except:
                original_urls = urls_to_process
        else:
            original_urls = [args.start_url] if args.start_url else urls_to_process
        
        await update_output_file_incrementally(args.output, new_domains, existing_domains, dead_domains, args, original_urls)

if __name__ == "__main__":
    asyncio.run(main())
