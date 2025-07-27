import argparse
import re
import json
import asyncio
import os
import dns.resolver
import logging
import random
import time
import shutil
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs
from collections import deque
from playwright.async_api import async_playwright
from typing import Set, List, Tuple, Optional, Dict, Any

# Custom logging formatter for better readability
class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors and website-specific formatting"""
    
    COLORS = {
        'DEBUG': '\033[36m',    # Cyan
        'INFO': '\033[32m',     # Green
        'WARNING': '\033[33m',  # Yellow
        'ERROR': '\033[31m',    # Red
        'CRITICAL': '\033[35m', # Magenta
        'RESET': '\033[0m'      # Reset
    }
    
    def format(self, record):
        # Add color if outputting to console
        if hasattr(record, 'website'):
            record.msg = f"[{record.website}] {record.msg}"
        
        formatted = super().format(record)
        
        if hasattr(self, '_use_colors') and self._use_colors:
            color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
            formatted = f"{color}{formatted}{self.COLORS['RESET']}"
        
        return formatted

def setup_enhanced_logging(log_file=None, log_level=logging.INFO):
    """Setup enhanced logging with structured output for website tracking"""
    
    # Create formatters
    detailed_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    simple_format = '%(levelname)s - %(message)s'
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Clear existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Console handler with colors
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_formatter = ColoredFormatter(detailed_format)
    console_formatter._use_colors = True
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    # File handler if specified
    if log_file:
        try:
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir)
            
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(log_level)
            file_formatter = ColoredFormatter(detailed_format)
            file_handler.setFormatter(file_formatter)
            root_logger.addHandler(file_handler)
            
            logging.info(f"Enhanced logging initialized - File: {log_file}")
        except Exception as e:
            logging.warning(f"Could not setup file logging: {e}")
    
    return root_logger

def get_website_logger(url):
    """Get a logger specifically for a website with context"""
    domain = urlparse(url).netloc.replace('www.', '')
    logger = logging.getLogger(f"crawler.{domain}")
    
    # Add website context to all log records
    class WebsiteAdapter(logging.LoggerAdapter):
        def process(self, msg, kwargs):
            return msg, kwargs
    
    adapter = WebsiteAdapter(logger, {'website': domain})
    
    # Override the adapter to add website info to extra
    original_process = adapter.process
    def enhanced_process(msg, kwargs):
        msg, kwargs = original_process(msg, kwargs)
        if 'extra' not in kwargs:
            kwargs['extra'] = {}
        kwargs['extra']['website'] = domain
        return msg, kwargs
    
    adapter.process = enhanced_process
    return adapter

def find_embedded_domains(html_text: str) -> List[str]:
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
    
    return sorted(list(found_domains))

def normalize_url(url: str) -> str:
    """Normalize URL for deduplication by removing common variations."""
    parsed = urlparse(url)
    
    # Normalize domain (remove www)
    domain = parsed.netloc.lower().replace('www.', '')
    
    # Normalize path (remove trailing slash, convert to lowercase)
    path = parsed.path.rstrip('/').lower()
    
    # Parse and normalize query parameters
    query_params = {}
    if parsed.query:
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

def is_similar_url(url1: str, url2: str, similarity_threshold: float = 0.8) -> bool:
    """Check if two URLs are very similar (e.g., just different query params or regional variants)."""
    parsed1 = urlparse(url1)
    parsed2 = urlparse(url2)
    
    # Different domains are not similar
    if parsed1.netloc.lower() != parsed2.netloc.lower():
        return False
    
    path1 = parsed1.path.rstrip('/').lower()
    path2 = parsed2.path.rstrip('/').lower()
    
    # Check for regional/language variants (e.g., /en-us/page vs /fr-fr/page)
    lang_pattern = r'^/[a-z]{2}-[a-z]{2}(/.*)?$'
    
    if re.match(lang_pattern, path1) and re.match(lang_pattern, path2):
        # Extract the path after language code
        path1_without_lang = re.sub(r'^/[a-z]{2}-[a-z]{2}', '', path1)
        path2_without_lang = re.sub(r'^/[a-z]{2}-[a-z]{2}', '', path2)
        if path1_without_lang == path2_without_lang:
            return True
    
    # Check for very similar paths using Levenshtein distance for better accuracy
    if path1 and path2:
        try:
            # Try to use rapidfuzz for more accurate similarity calculation
            from rapidfuzz import fuzz
            similarity_ratio = fuzz.ratio(path1, path2)
            if similarity_ratio >= (similarity_threshold * 100):
                return True
        except ImportError:
            # Fallback to basic character-based similarity if rapidfuzz not available
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

async def crawl_domains(start_url, max_pages, page, rate_limit=2.0, navigation_timeout=15.0, logger=None):
    """Crawls a website by following links to find domains."""
    if logger is None:
        logger = logging.getLogger(__name__)
    
    urls_to_crawl = deque([start_url])
    crawled_urls = set()
    found_subdomains = set()
    found_external_domains = set()
    base_domain = urlparse(start_url).netloc.replace("www.", "")
    ignored_extensions = ['.png', '.jpg', '.css', '.js', '.ico', '.svg', '.xml', '.pdf', '.zip']
    skipped_similar = 0

    logger.info(f"Starting crawl - Target: {max_pages} pages, Rate limit: {rate_limit}s")

    while urls_to_crawl and len(crawled_urls) < max_pages:
        url = urls_to_crawl.popleft()
        if url in crawled_urls: 
            continue

        page_num = len(crawled_urls) + 1
        logger.info(f"Crawling page {page_num}/{max_pages}: {url}")
        crawled_urls.add(url)
        
        try:
            # Skip problematic URLs that typically require authentication or cause issues
            problematic_paths = ['/submit', '/notifications', '/settings', '/account', '/login', '/register']
            if any(path in url.lower() for path in problematic_paths):
                logger.debug(f"Skipping authentication-required page: {url}")
                continue
            
            # Add configurable delay to prevent rapid navigation issues
            logger.debug(f"Applying rate limit delay: {rate_limit}s")
            await asyncio.sleep(rate_limit)
            
            # Navigate to the page with better error handling
            try:
                nav_timeout_ms = int(navigation_timeout * 1000)  # Convert to milliseconds
                logger.debug(f"Navigating to {url} (timeout: {navigation_timeout}s)")
                response = await page.goto(url, wait_until='domcontentloaded', timeout=nav_timeout_ms)
                
                if response:
                    logger.debug(f"Response status: {response.status}")
                else:
                    logger.warning(f"No response received for {url}")
                    
            except Exception as nav_error:
                if "net::ERR_NAME_NOT_RESOLVED" in str(nav_error):
                    logger.warning(f"DNS resolution failed for {url} (subdomain may not exist)")
                elif "interrupted by another navigation" in str(nav_error):
                    logger.warning(f"Navigation interrupted by redirect for {url}")
                    # Try to get the current page after redirect
                    try:
                        await page.wait_for_load_state('domcontentloaded', timeout=5000)
                        current_url = page.url
                        if current_url != url and not current_url.startswith('chrome-error://'):
                            logger.info(f"Redirected to: {current_url}")
                            # Extract domains from the redirected page
                            html_content = await page.content()
                            soup = BeautifulSoup(html_content, 'html.parser')
                        else:
                            logger.warning(f"Redirect failed or led to error page")
                            continue
                    except Exception:
                        logger.error(f"Failed to handle redirect for {url}")
                        continue
                else:
                    logger.error(f"Navigation failed for {url}: {nav_error}")
                    continue
            else:
                if not response or response.status >= 400:
                    logger.warning(f"Failed to load page {url} (status: {response.status if response else 'unknown'})")
                    continue
                    
                # Check content type
                content_type = response.headers.get('content-type', '')
                if 'text/html' not in content_type:
                    logger.debug(f"Skipping non-HTML content: {url} (content-type: {content_type})")
                    continue
                    
                # Wait a bit for any dynamic content to load
                await asyncio.sleep(rate_limit)
                
                # Get the page content after JavaScript execution
                html_content = await page.content()
                soup = BeautifulSoup(html_content, 'html.parser')
            
        except Exception as e:
            logger.error(f"Unexpected error processing {url}: {e}")
            continue

        # Extract links from various elements
        links_found = 0
        new_internal_links = 0
        new_external_domains = 0
        
        for tag in soup.find_all(['a', 'link'], href=True) + soup.find_all(['script', 'img'], src=True):
            link = tag.get('href') or tag.get('src')
            if not link or link.startswith(('mailto:', 'tel:', 'javascript:', '#')): 
                continue

            absolute_link = urljoin(url, link)
            domain = urlparse(absolute_link).netloc

            if not domain: 
                continue

            links_found += 1

            if domain.endswith(base_domain):
                if domain != base_domain: 
                    if domain not in found_subdomains:
                        found_subdomains.add(domain)
                        logger.debug(f"New subdomain found: {domain}")
                        
                if (absolute_link not in crawled_urls and
                    absolute_link not in urls_to_crawl and
                    not any(absolute_link.lower().endswith(ext) for ext in ignored_extensions)):
                    
                    # Check if this URL is too similar to already processed URLs
                    should_skip, reason = should_skip_url(absolute_link, crawled_urls, urls_to_crawl)
                    if should_skip:
                        skipped_similar += 1
                        # Log the first few skips for debugging
                        if skipped_similar <= 3:
                            logger.debug(f"Skipping similar URL {absolute_link}: {reason}")
                    else:
                        urls_to_crawl.append(absolute_link)
                        new_internal_links += 1
            else:
                if domain not in found_external_domains:
                    found_external_domains.add(domain)
                    new_external_domains += 1
                    logger.debug(f"New external domain found: {domain}")
        
        logger.info(f"Page {page_num} processed: {links_found} links, {new_internal_links} new internal, {new_external_domains} new external domains")
    
    if skipped_similar > 0:
        logger.info(f"Crawling efficiency: Skipped {skipped_similar} similar/duplicate URLs")
    
    logger.info(f"Crawl completed: {len(crawled_urls)} pages crawled, {len(found_subdomains)} subdomains, {len(found_external_domains)} external domains")
                
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

async def validate_domain_dns_with_retry(domain, max_retries=3, retry_delay=2, logger=None):
    """Validate if a domain exists via DNS lookup with retry logic."""
    if logger is None:
        logger = logging.getLogger(__name__)
        
    for attempt in range(max_retries):
        try:
            # Try both A and AAAA records
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: dns.resolver.resolve(domain, 'A')
            )
            logger.debug(f"DNS validation successful for {domain} (A record)")
            return True
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            try:
                # Try AAAA record (IPv6)
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: dns.resolver.resolve(domain, 'AAAA')
                )
                logger.debug(f"DNS validation successful for {domain} (AAAA record)")
                return True
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
                if attempt < max_retries - 1:
                    logger.debug(f"DNS validation failed for {domain}, attempt {attempt + 1}/{max_retries}, retrying in {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
                    continue
                logger.debug(f"DNS validation failed for {domain} - domain does not exist")
                return False
        except Exception as e:
            if attempt < max_retries - 1:
                logger.debug(f"DNS error for {domain}: {e}, retrying...")
                await asyncio.sleep(retry_delay)
                continue
            # For any other DNS errors, assume domain is valid to be safe
            logger.warning(f"DNS validation inconclusive for {domain}: {e} (keeping domain)")
            return True
    
    return False

async def validate_domain_dns(domain, logger=None):
    """Validate if a domain exists via DNS lookup."""
    return await validate_domain_dns_with_retry(domain, logger=logger)

def load_existing_domains(output_file):
    """Load existing domains from output file with improved parsing."""
    logger = logging.getLogger(__name__)
    existing_domains = set()
    if not os.path.exists(output_file):
        return existing_domains
    
    try:
        with open(output_file, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                original_line = line
                line = line.strip()
                
                # Skip comments and empty lines
                if not line or line.startswith('#'):
                    continue
                
                domain = None
                
                # Handle different formats
                if line.startswith('- '):
                    # Markdown list format
                    domain = line[2:].strip()
                elif line.startswith('  - '):
                    # Indented markdown list format
                    domain = line[4:].strip()
                elif '.' in line and not line.startswith('##'):
                    # Plain domain format
                    domain = line.strip()
                
                if domain:
                    # Validate domain format
                    if re.match(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', domain):
                        existing_domains.add(domain.lower())
                    else:
                        logger.warning(f"Invalid domain format on line {line_num}: {domain}")
        
        logger.info(f"Loaded {len(existing_domains)} existing domains from {output_file}")
        return existing_domains
        
    except UnicodeDecodeError as e:
        logger.error(f"Encoding error reading {output_file}: {e}")
        return set()
    except Exception as e:
        logger.error(f"Could not load existing domains from {output_file}: {e}")
        return set()

async def validate_existing_domains(existing_domains, conservative_mode=True):
    """Validate existing domains via DNS and remove dead ones with conservative approach."""
    logger = logging.getLogger(__name__)
    
    if not existing_domains:
        return set(), set()
    
    logger.info(f"Validating {len(existing_domains)} existing domains via DNS...")
    
    # Create semaphore to limit concurrent DNS queries
    dns_semaphore = asyncio.Semaphore(10)  # Reduced from 20 to be more conservative
    
    async def check_domain(domain):
        async with dns_semaphore:
            try:
                is_valid = await validate_domain_dns(domain, logger)
                return domain, is_valid
            except Exception as e:
                logger.warning(f"DNS check failed for {domain}: {e}")
                # In conservative mode, keep domains if validation fails
                return domain, conservative_mode
    
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
                logger.info(f"Dead domain removed: {domain}")
        else:
            logger.error(f"DNS validation failed: {result}")
    
    if dead_domains:
        logger.info(f"Removed {len(dead_domains)} dead domains")
    logger.info(f"Validated {len(valid_domains)} domains as active")
    
    return valid_domains, dead_domains

def should_skip_url_based_on_existing_domains(url, existing_domains, dual_stack_mode=False):
    """Determine if a URL should be skipped because we already know its domains."""
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.replace('www.', '')
    
    # In dual-stack mode, don't skip URLs since IPv4/IPv6 might reveal different domains
    if dual_stack_mode:
        return False, None
    
    # If the main domain is already known, we likely have most of its subdomains
    if domain in existing_domains:
        return True, f"main domain {domain} already in existing domains"
    
    # Check if this is a subdomain of an already known domain
    for existing_domain in existing_domains:
        if domain.endswith('.' + existing_domain):
            return True, f"subdomain of known domain {existing_domain}"
    
    return False, None

def create_backup_file(output_file):
    """Create a backup of the output file before modifying it."""
    logger = logging.getLogger(__name__)
    
    if not os.path.exists(output_file):
        return None
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"{output_file}.backup_{timestamp}"
    
    try:
        import shutil
        shutil.copy2(output_file, backup_file)
        logger.info(f"Created backup: {backup_file}")
        return backup_file
    except Exception as e:
        logger.warning(f"Could not create backup: {e}")
        return None

async def update_output_file_incrementally(output_file, new_domains, existing_domains, dead_domains, args, input_urls=None):
    """Update output file by removing dead domains and adding new ones with atomic writes."""
    logger = logging.getLogger(__name__)
    
    if not output_file:
        return
    
    # Create backup before modifying
    backup_file = create_backup_file(output_file)
    
    # Calculate domains to write
    domains_to_write = (existing_domains - dead_domains) | new_domains
    total_new = len(new_domains)
    total_removed = len(dead_domains)
    
    # Get current timestamp
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    
    # Write to temporary file first for atomic update
    temp_file = f"{output_file}.tmp"
    
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
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
                f.write(f"#   Dual-stack mode: {args.dual_stack}\n")
                if args.url_file:
                    f.write(f"#   URL file: {args.url_file}\n")
                if args.cookies:
                    f.write(f"#   Cookie file: {args.cookies}\n")
                if args.log_file:
                    f.write(f"#   Log file: {args.log_file}\n")
                if backup_file:
                    f.write(f"#   Backup file: {backup_file}\n")
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
                f.write(f"#   Dual-stack mode: {args.dual_stack}\n")
                if backup_file:
                    f.write(f"#   Backup file: {backup_file}\n")
                f.write(f"# ================================================\n\n")
                
                f.write("## Summary\n")
                f.write(f"- Last updated: {current_time}\n")
                f.write(f"- Total organization domains: {len(domains_to_write)}\n")
                f.write(f"- New domains added this run: {total_new}\n")
                f.write(f"- Dead domains removed this run: {total_removed}\n\n")
                
                f.write("## Organization FQDN List\n")
                for domain in sorted(domains_to_write):
                    f.write(f"{domain}\n")
        
        # Atomic move from temp file to final file
        import shutil
        shutil.move(temp_file, output_file)
        
        # Log the update
        logger.info(f"Updated output file: {output_file}")
        logger.info(f"Added {total_new} new domains, removed {total_removed} dead domains")
        logger.info(f"Total domains in file: {len(domains_to_write)}")
        
        print(f"\n📄 Updated {output_file}:")
        if total_new > 0:
            print(f"   ✅ Added {total_new} new domains")
        if total_removed > 0:
            print(f"   🗑️  Removed {total_removed} dead domains")
        print(f"   📊 Total domains: {len(domains_to_write)}")
        print(f"   🕒 Last updated: {current_time}")
        if backup_file:
            print(f"   💾 Backup created: {backup_file}")
        
    except Exception as e:
        # Clean up temp file if it exists
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass
        
        error_msg = f"Error updating output file: {e}"
        logger.error(error_msg)
        print(f"❌ {error_msg}")
        
        # Restore from backup if available
        if backup_file and os.path.exists(backup_file):
            try:
                import shutil
                shutil.copy2(backup_file, output_file)
                print(f"   🔄 Restored from backup: {backup_file}")
                logger.info(f"Restored from backup: {backup_file}")
            except Exception as restore_error:
                print(f"   ❌ Could not restore from backup: {restore_error}")
                logger.error(f"Could not restore from backup: {restore_error}")

async def process_single_url(url, url_index, total_urls, args, semaphore):
    """Process a single URL and return its results with enhanced logging."""
    async with semaphore:  # Limit concurrent browser instances
        # Get website-specific logger
        logger = get_website_logger(url)
        
        # Ensure URL has protocol
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
            logger.info(f"Added https:// protocol: {url}")
        
        logger.info(f"Starting processing [{url_index + 1}/{total_urls}]")
        
        # If dual-stack is enabled, crawl both IPv4 and IPv6
        if args.dual_stack:
            logger.info("Dual-stack mode: Crawling both IPv4 and IPv6")
            
            ipv4_result = await process_single_url_with_ip_version(url, url_index, total_urls, args, "ipv4", logger)
            ipv6_result = await process_single_url_with_ip_version(url, url_index, total_urls, args, "ipv6", logger)
            
            # Combine results from both IP versions
            if ipv4_result and ipv6_result:
                combined_result = {
                    'url': url,
                    'subdomains': sorted(list(set(ipv4_result['subdomains'] + ipv6_result['subdomains']))),
                    'external_domains': sorted(list(set(ipv4_result['external_domains'] + ipv6_result['external_domains']))),
                    'related_domains': sorted(list(set(ipv4_result['related_domains'] + ipv6_result['related_domains']))),
                    'parent_domains': sorted(list(set(ipv4_result['parent_domains'] + ipv6_result['parent_domains']))),
                    'consolidated_fqdns': set(ipv4_result['consolidated_fqdns']) | set(ipv6_result['consolidated_fqdns'])
                }
                
                logger.info(f"Combined Results:")
                logger.info(f"  • IPv4 domains: {len(ipv4_result['consolidated_fqdns'])}")
                logger.info(f"  • IPv6 domains: {len(ipv6_result['consolidated_fqdns'])}")
                logger.info(f"  • Total unique domains: {len(combined_result['consolidated_fqdns'])}")
                
                return combined_result
            elif ipv4_result:
                logger.warning("IPv6 failed, using IPv4 results only")
                return ipv4_result
            elif ipv6_result:
                logger.warning("IPv4 failed, using IPv6 results only")
                return ipv6_result
            else:
                logger.error("Both IPv4 and IPv6 failed")
                return None
        else:
            # Single IP version mode (existing behavior)
            return await process_single_url_with_ip_version(url, url_index, total_urls, args, None, logger)

async def process_single_url_with_ip_version(url, url_index, total_urls, args, ip_version, logger):
    """Process a single URL with a specific IP version."""
    ip_label = f" ({ip_version.upper()})" if ip_version else ""
    logger.info(f"Connecting to {url}{ip_label}...")
    
    # Set up persistent storage for this URL
    storage_state_file = None
    if not args.no_persist_cookies:
        storage_dir = os.path.join(os.getcwd(), '.browser_data')
        os.makedirs(storage_dir, exist_ok=True)
        
        domain = urlparse(url).netloc.replace('www.', '')
        storage_state_file = os.path.join(storage_dir, f'{domain}_storage.json')
        
        if args.clear_cookies and os.path.exists(storage_state_file):
            os.remove(storage_state_file)
            logger.info(f"Cleared existing browser data for {domain}")
        
        if os.path.exists(storage_state_file):
            logger.debug(f"Loading existing browser data from {storage_state_file}")
        else:
            logger.debug(f"Creating new browser data file: {storage_state_file}")
    
    # Launch browser for this URL
    async with async_playwright() as p:
        # Configure browser launch arguments for IP version
        browser_args = [
            '--no-sandbox',
            '--disable-blink-features=AutomationControlled',
            '--disable-extensions',
            '--disable-web-security',
            '--disable-features=VizDisplayCompositor',
            '--no-first-run',
            '--disable-infobars',
            '--disable-notifications'
        ]
        
        # Add IP version specific arguments
        if ip_version == "ipv4":
            browser_args.extend([
                '--disable-ipv6',
                '--force-ipv4-only'
            ])
        elif ip_version == "ipv6":
            browser_args.extend([
                '--disable-ipv4',
                '--force-ipv6-only'
            ])
        
        logger.debug(f"Launching browser with {len(browser_args)} arguments")
        browser = await p.chromium.launch(
            headless=args.headless,
            args=browser_args
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
                logger.debug("Loading existing browser state")
            
            context = await browser.new_context(**context_options)
            page = await context.new_page()
            
            # Import cookies if provided (for first URL only)
            if args.cookies and url_index == 0:
                try:
                    logger.info(f"Loading cookies from {args.cookies}...")
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
                    logger.info(f"Successfully imported {len(playwright_cookies)} cookies")
                    
                    if storage_state_file:
                        try:
                            await context.storage_state(path=storage_state_file)
                            logger.debug(f"Imported cookies saved to persistent storage: {storage_state_file}")
                        except Exception as e:
                            logger.warning(f"Could not save imported cookies: {e}")
                            
                except FileNotFoundError:
                    logger.error(f"Cookie file not found: {args.cookies}")
                    return None
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON in cookie file: {e}")
                    return None
                except Exception as e:
                    logger.error(f"Error importing cookies: {e}")
                    return None
            
            try:
                logger.info("Phase 1: Loading initial page...")
                logger.debug(f"Navigating to {url} (timeout: {args.navigation_timeout}s)")
                nav_timeout = args.navigation_timeout * 1000  # Convert to milliseconds
                response = await page.goto(url, wait_until='domcontentloaded', timeout=nav_timeout)
                
                if response:
                    logger.info(f"Initial page loaded - Status: {response.status}")
                else:
                    logger.error(f"No response received for {url}")
                    return None
                
                if not response or response.status >= 400:
                    logger.error(f"Failed to load {url} - Status: {response.status}")
                    return None
                
                # Check for embedded config
                logger.info("Phase 2: Checking for embedded domain configuration...")
                html_content = await page.content()
                embedded_domains = find_embedded_domains(html_content)
                
                if embedded_domains:
                    logger.info(f"Success! Found embedded domain config with {len(embedded_domains)} domains")
                    subdomains = embedded_domains
                    external_domains = []
                else:
                    logger.info("Phase 3: No embedded config found, starting crawler...")
                    subdomains, external_domains = await crawl_domains(url, args.pages, page, args.rate_limit, args.navigation_timeout, logger)
                
                # Get base domain for categorization
                base_domain = urlparse(url).netloc.replace('www.', '')
                logger.debug(f"Base domain: {base_domain}")
                
                # Categorize domains for this URL
                if external_domains:
                    logger.info("Phase 4: Categorizing discovered domains...")
                    related_domains, third_party_domains, parent_domains = categorize_domains(base_domain, subdomains, external_domains)
                    logger.debug(f"Categorization complete: {len(related_domains)} related, {len(third_party_domains)} third-party, {len(parent_domains)} parent")
                else:
                    related_domains, third_party_domains, parent_domains = [], [], []
                
                # Create consolidated FQDN list for this URL
                url_consolidated_fqdns = set(subdomains + parent_domains + related_domains + [base_domain])
                
                logger.info(f"Results Summary{ip_label}:")
                logger.info(f"  • Subdomains: {len(subdomains)}")
                logger.info(f"  • Related domains: {len(related_domains)}")
                logger.info(f"  • Organization FQDNs: {len(url_consolidated_fqdns)}")
                logger.info(f"Processing completed successfully")
                
                return {
                    'url': url,
                    'subdomains': subdomains,
                    'external_domains': external_domains,
                    'related_domains': related_domains,
                    'parent_domains': parent_domains,
                    'consolidated_fqdns': url_consolidated_fqdns
                }
                
            except Exception as e:
                logger.error(f"Error during processing: {e}")
                return None
            finally:
                # Save storage state
                if storage_state_file:
                    try:
                        await context.storage_state(path=storage_state_file)
                        logger.debug(f"Browser data saved to {storage_state_file}")
                    except Exception as e:
                        logger.warning(f"Could not save browser data: {e}")
                        
        finally:
            await browser.close()
            logger.debug("Browser closed")

async def main():
    parser = argparse.ArgumentParser(description="Finds domains using config extraction with a crawler fallback.")
    parser.add_argument("start_url", nargs='?', help="The URL to start from (e.g., https://www.reddit.com). Optional if --url-file is provided.")
    parser.add_argument("-p", "--pages", type=int, default=10, help="Max pages to crawl if no config is found.")
    parser.add_argument("-o", "--output", help="Optional file to save the results.")
    parser.add_argument("--headless", action="store_true", default=True, help="Run browser in headless mode (default).")
    parser.add_argument("--no-headless", action="store_false", dest="headless", help="Run browser with UI visible.")
    parser.add_argument("--ipv6", action="store_true", help="Alternate between IPv4 and IPv6 for retry attempts.")
    parser.add_argument("--dual-stack", action="store_true", help="Crawl each URL using both IPv4 and IPv6 for complete domain discovery.")
    parser.add_argument("--no-persist-cookies", action="store_true", help="Disable storing cookies and browser data between sessions.")
    parser.add_argument("--clear-cookies", action="store_true", help="Clear stored cookies and browser data before starting.")
    parser.add_argument("--manual-login", action="store_true", help="Pause after loading page to allow manual login before crawling.")
    parser.add_argument("--cookies", help="Path to JSON file containing cookies to import (exported from browser extensions).")
    parser.add_argument("--fqdn-list", action="store_true", help="Output a consolidated FQDN list of all organization-related domains (excludes third-party).")
    parser.add_argument("--url-file", help="Path to file containing URLs to crawl (one per line). If specified, start_url is ignored.")
    parser.add_argument("--concurrency", type=int, default=3, help="Number of concurrent browser instances for batch processing (default: 3, max: 10).")
    parser.add_argument("--log-file", help="Path to log file for unattended runs. If not specified, logs to console only.")
    parser.add_argument("--conservative-dns", action="store_true", default=True, help="Use conservative DNS validation (keep domains if validation fails).")
    parser.add_argument("--navigation-timeout", type=int, default=30, help="Timeout in seconds for page navigation (default: 30).")
    parser.add_argument("--rate-limit", type=float, default=2.0, help="Delay in seconds between page requests (default: 2.0).")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging (DEBUG level).")
    args = parser.parse_args()
    
    # Setup enhanced logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    setup_enhanced_logging(args.log_file, log_level)
    
    # Get main logger
    logger = logging.getLogger(__name__)
    
    # Log script start
    logger.info("="*60)
    logger.info("DOMAIN CRAWLER STARTED")
    logger.info("="*60)
    logger.info(f"Configuration: {vars(args)}")
    
    # Validate concurrency limit
    if args.concurrency > 10:
        logger.warning("Concurrency limited to maximum of 10 to prevent resource exhaustion")
        args.concurrency = 10
    elif args.concurrency < 1:
        args.concurrency = 1
    
    # Validate that either start_url or url_file is provided
    if not args.start_url and not args.url_file:
        parser.error("Either start_url or --url-file must be provided")
    
    if args.url_file and args.start_url:
        logger.warning("Both start_url and --url-file provided. Using --url-file and ignoring start_url.")
    
    # Determine URLs to process
    urls_to_process = []
    if args.url_file:
        try:
            with open(args.url_file, 'r') as f:
                urls_to_process = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
            logger.info(f"Batch processing {len(urls_to_process)} URLs from {args.url_file}")
            if len(urls_to_process) == 0:
                logger.error(f"No valid URLs found in {args.url_file}")
                return
        except FileNotFoundError:
            logger.error(f"URL file not found: {args.url_file}")
            return
        except Exception as e:
            logger.error(f"Error reading URL file: {e}")
            return
    else:
        urls_to_process = [args.start_url]
        logger.info(f"Single URL analysis: {args.start_url}")
    
    # Load existing domains from output file if it exists
    existing_domains = set()
    dead_domains = set()
    if args.output:
        logger.info("Loading existing domains from output file...")
        existing_domains = load_existing_domains(args.output)
        if existing_domains:
            # Validate existing domains via DNS
            existing_domains, dead_domains = await validate_existing_domains(existing_domains, args.conservative_dns)

    # Filter URLs to process based on existing domains
    filtered_urls = []
    skipped_urls = []
    
    for url in urls_to_process:
        should_skip, reason = should_skip_url_based_on_existing_domains(url, existing_domains, args.dual_stack)
        if should_skip:
            skipped_urls.append((url, reason))
            logger.info(f"Skipping {url}: {reason}")
        else:
            filtered_urls.append(url)
    
    if skipped_urls:
        logger.info(f"Efficiency: Skipped {len(skipped_urls)} URLs with known domains")
    
    # Update URLs to process with filtered list
    urls_to_process = filtered_urls
    
    if not urls_to_process:
        logger.info("All URLs skipped - domains already known and validated")
        if args.output and dead_domains:
            # Still update output file to remove dead domains
            await update_output_file_incrementally(args.output, set(), existing_domains, dead_domains, args)
        return

    # Randomize URL processing order to avoid detection patterns
    if len(urls_to_process) > 1:
        original_order = urls_to_process.copy()
        random.shuffle(urls_to_process)
        logger.info(f"Randomized processing order for {len(urls_to_process)} URLs to avoid detection patterns")
        logger.debug(f"Original URL order: {original_order}")
        logger.debug(f"Randomized URL order: {urls_to_process}")

    # Combined results for all URLs
    all_subdomains = set()
    all_external_domains = set()
    all_consolidated_fqdns = set()

    # Determine if we should use concurrent processing
    if len(urls_to_process) > 1 and args.concurrency > 1:
        logger.info(f"Concurrent processing enabled: {args.concurrency} browser instances")
        
        # Create semaphore to limit concurrent browser instances
        semaphore = asyncio.Semaphore(args.concurrency)
        
        # Create tasks for concurrent processing
        tasks = []
        for url_index, url in enumerate(urls_to_process):
            task = process_single_url(url, url_index, len(urls_to_process), args, semaphore)
            tasks.append(task)
        
        # Process all URLs concurrently
        logger.info("Starting concurrent URL processing...")
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
                logger.error(f"Task failed with exception: {result}")
        
        logger.info(f"Concurrent processing completed: {successful_results}/{len(urls_to_process)} URLs processed successfully")
        
    else:
        # Single URL or sequential processing
        if len(urls_to_process) == 1:
            logger.info(f"Single URL processing mode")
        else:
            logger.info(f"Sequential processing mode (concurrency disabled or set to 1)")
        
        semaphore = asyncio.Semaphore(1)  # Sequential processing
        
        for url_index, url in enumerate(urls_to_process):
            logger.info(f"\n{'='*60}")
            logger.info(f"PROCESSING URL {url_index + 1}/{len(urls_to_process)}")
            logger.info(f"{'='*60}")
            
            result = await process_single_url(url, url_index, len(urls_to_process), args, semaphore)
            if result:
                all_subdomains.update(result['subdomains'])
                all_external_domains.update(result['external_domains'])
                all_consolidated_fqdns.update(result['consolidated_fqdns'])

    # Final aggregated results
    if len(urls_to_process) > 1:
        logger.info(f"\n{'='*60}")
        logger.info("AGGREGATED RESULTS FROM ALL URLs")
        logger.info(f"{'='*60}")
    
    # Convert sets back to lists and categorize aggregated results
    final_subdomains = sorted(list(all_subdomains))
    final_external_domains = sorted(list(all_external_domains))
    final_consolidated_fqdns = sorted(list(all_consolidated_fqdns))
    
    # Validate newly discovered domains via DNS before adding to output
    if final_consolidated_fqdns:
        logger.info(f"Validating {len(final_consolidated_fqdns)} discovered domains via DNS...")
        
        # Create semaphore to limit concurrent DNS queries
        dns_semaphore = asyncio.Semaphore(20)  # Limit to 20 concurrent DNS queries
        
        async def check_discovered_domain(domain):
            async with dns_semaphore:
                try:
                    is_valid = await validate_domain_dns(domain, logger)
                    return domain, is_valid
                except Exception as e:
                    logger.warning(f"DNS check failed for {domain}: {e}")
                    return domain, args.conservative_dns  # Use conservative mode setting
        
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
                    logger.info(f"Excluded invalid domain: {domain}")
            else:
                logger.error(f"DNS validation failed: {result}")
        
        # Update the final lists to only include valid domains
        final_consolidated_fqdns = sorted(valid_discovered_domains)
        
        # Also filter the individual lists
        valid_domains_set = set(valid_discovered_domains)
        final_subdomains = [d for d in final_subdomains if d in valid_domains_set]
        final_external_domains = [d for d in final_external_domains if d in valid_domains_set]
        
        if invalid_discovered_domains:
            logger.info(f"Excluded {len(invalid_discovered_domains)} domains that don't resolve in DNS")
        
        logger.info(f"Validated {len(valid_discovered_domains)} domains and ready for output")
    
    # Summary statistics
    logger.info(f"\n📈 FINAL SUMMARY:")
    logger.info(f"  • Total URLs processed: {len(urls_to_process)}")
    logger.info(f"  • Total unique subdomains: {len(final_subdomains)}")
    logger.info(f"  • Total unique external domains: {len(final_external_domains)}")
    logger.info(f"  • Total organization FQDNs: {len(final_consolidated_fqdns)}")
    if args.dual_stack:
        logger.info(f"  • Dual-stack mode: IPv4 and IPv6 crawling enabled")
    
    # Display results
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

    logger.info("="*60)
    logger.info("DOMAIN CRAWLER COMPLETED")
    logger.info("="*60)

if __name__ == "__main__":
    asyncio.run(main())
