from bs4 import BeautifulSoup
import openai
import asyncio
import json
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
import time
import re
import os
from sqlalchemy.orm import joinedload
from urllib.parse import unquote, urlparse, parse_qs
import urllib.parse
import numpy as np
from datetime import datetime, timedelta
import pandas as pd
from sqlalchemy import or_, and_, desc


from app.models import Product, AmazonProduct, ProductMatch
from app.db import SessionLocal
import keepa
import logging

# Set up logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()


# Function to fetch historical data from Keepa API, including offer data
def fetch_historical_data(asin):
    try:
        domain_code = 'US'  # Correct domain code for Amazon US
        stats_days = 90  # Use 90 days of statistics
        offers = 20  # Fetch up to 20 offers
        days = 365  # Fetch 365 days of history

        # Fetch data with the history and days parameters
        logger.debug(f"Fetching data from Keepa for ASIN: {asin}")
        product_data = keepa_api.query(asin, domain=domain_code, stats=stats_days, offers=offers, buybox=True, history=1, days=days)
        
        logger.debug(f"Data fetched for ASIN: {asin}")
        return product_data[0] if product_data else None
    except Exception as e:
        logger.error(f"Error fetching data from Keepa: {e}")
        return None


# Convert keepa time to unix time
def keepaTimeMinutesToUnixTime(keepaMinutes):
    return (21564000 + int(keepaMinutes)) * 60000

# Transform prices data to list
def transformKeepaHistoryList(buy_box_seller_history):
    return [(datetime.utcfromtimestamp(keepaTimeMinutesToUnixTime(keepaMinutes) / 1000), val) for
                      keepaMinutes, val in zip(buy_box_seller_history[::2], buy_box_seller_history[1::2])]

def fill_missing_days(df, last_n_days=90):
    # Create a full date range from today back to last_n_days (ignoring time)
    end_date = pd.Timestamp(datetime.utcnow().date())  # Convert to pandas Timestamp and remove time
    start_date = end_date - timedelta(days=last_n_days)
    all_dates = pd.date_range(start=start_date, end=end_date)

    # Normalize the 'date' column to truncate to just the date (ignoring hours and minutes)
    df['date'] = df['date'].dt.normalize()

    # Drop duplicates to ensure one entry per day
    df = df.drop_duplicates(subset='date', keep='last')

    # Set the date as index for the history dataframe
    df.set_index('date', inplace=True)

    # Reindex to ensure every day is represented, fill missing seller with the previous value
    df = df.reindex(all_dates, method='ffill').reset_index()
    
    # Rename columns
    df.columns = ['date', 'seller']
    
    return df



def get_amazon_buy_box_count(buy_box_seller_history):
    logger.debug("Checking if Amazon held the buy box at least once in the last 90 days...")

    if not buy_box_seller_history:
        logger.debug("No buy box seller history available.")
        return -1

    # Transform history list into a dataframe
    buyboxhistory = transformKeepaHistoryList(buy_box_seller_history)
    df_buyboxhistory = pd.DataFrame(buyboxhistory, columns=['date', 'seller'])
    # print(df_buyboxhistory.to_string())

    # Fill in missing days and limit to the last 90 days
    df_filled = fill_missing_days(df_buyboxhistory)

    # Count how many days Amazon (ATVPDKIKX0DER) held the buy box
    amazon_seller_id = 'ATVPDKIKX0DER'
    amazon_buy_box_days = df_filled['seller'].value_counts().get(amazon_seller_id, 0)

    logger.debug(f"Amazon held the buy box for {amazon_buy_box_days} days in the last 90 days.")
    
    return amazon_buy_box_days
    
# Analyze product based on rules
def analyze_product(asin):
    logger.debug(f"Analyzing product with ASIN: {asin}")

    # Fetch Keepa data
    historical_data = fetch_historical_data(asin)
    if not historical_data:
        logger.error("Failed to fetch historical data from Keepa.")
        return -1, -1
    
    # Extract relevant information for analysis
    stats_data = historical_data.get('stats_parsed', {})
    buy_box_seller_history = historical_data.get('buyBoxSellerIdHistory', [])

    seller_count_history = historical_data['data'].get('COUNT_NEW', [])[-90:]  # New offer count history for the last 90 days

    amazon_buy_box_count = get_amazon_buy_box_count(buy_box_seller_history)
    current_sellers = seller_count_history[-1] if len(seller_count_history) > 0 else 0

    return amazon_buy_box_count, current_sellers

# Function to query all records, analyze each product, and update the fields
def analyze_and_update_products(session):
    # Query all products where amazon_buy_box_count is NULL (i.e., unprocessed)
    products = session.query(AmazonProduct).filter(or_(AmazonProduct.amazon_buy_box_count.is_(None), AmazonProduct.amazon_buy_box_count == -1)).all()

    for product in products:
        asin = product.asin
        logger.debug(f"Processing product with ASIN: {asin}")

        # Call the analyze_product function
        amazon_buy_box_count, current_sellers = analyze_product(asin)

        # Convert numpy.int64 to native Python int
        if isinstance(amazon_buy_box_count, np.integer):
            amazon_buy_box_count = int(amazon_buy_box_count)

        if isinstance(current_sellers, np.integer):
            current_sellers = int(current_sellers)

        print(f"Amazon buy box count: {amazon_buy_box_count}, Current sellers: {current_sellers}")
        
        # Check if the analysis was successful
        if amazon_buy_box_count is not None and current_sellers is not None:
            # Update the amazon_buy_box_count and current_sellers fields
            product.amazon_buy_box_count = amazon_buy_box_count
            product.current_sellers = current_sellers
            # Commit the changes to the database
            session.commit()
            logger.debug(f"Updated product {asin} with buy box count {amazon_buy_box_count} and current sellers {current_sellers}")
        else:
            logger.error(f"Failed to update product {asin} due to missing data.")

    logger.info("All products have been analyzed and updated.")



def extract_asin(url):
    # Try to extract ASIN directly from the path
    match = re.search(r'/([A-Z0-9]{10})(?:[/?]|$)', url)
    if match:
        return match.group(1)

    # If no match, check if ASIN is inside query parameters
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    if 'url' in query_params:
        decoded_url = unquote(query_params['url'][0])
        match = re.search(r'/([A-Z0-9]{10})(?:[/?]|$)', decoded_url)
        if match:
            return match.group(1)

    return None

# Function to search for the product on Amazon and return an array with URLs, titles, and image URLs for the first 10 results
def search_amazon_with_selenium(product):
    title = product.get('title')

    chrome_options = Options()
    chrome_options.debugger_address = "localhost:9222"  # Connect to running Chrome session
    chrome_options.headless = False  # To see browser actions

    driver = webdriver.Chrome(options=chrome_options)
    driver.execute_script("window.open('https://www.amazon.com', '_blank');")
    driver.switch_to.window(driver.window_handles[-1])

    time.sleep(5)

    try:
        wait = WebDriverWait(driver, 30)
        search_bar = wait.until(EC.presence_of_element_located((By.ID, 'twotabsearchtextbox')))
        search_bar = wait.until(EC.element_to_be_clickable((By.ID, 'twotabsearchtextbox')))
        search_bar.clear()
        search_bar.send_keys(title)
        search_bar.send_keys(Keys.RETURN)

        time.sleep(5)

        results = driver.find_elements(By.CSS_SELECTOR, '.s-main-slot .s-result-item')[:10]
        amazon_results = []

        for result in results:
            try:
                try:
                    product_url = result.find_element(By.CSS_SELECTOR, 'a.a-link-normal.s-no-outline').get_attribute('href')
                except Exception:
                    product_url = result.find_element(By.CSS_SELECTOR, 'a.a-link-normal').get_attribute('href')

                try:
                    product_title = result.find_element(By.CSS_SELECTOR, 'span.a-size-base-plus.a-color-base.a-text-normal').text
                except Exception:
                    product_title = result.find_element(By.CSS_SELECTOR, 'span.a-text-normal').text

                try:
                    image_url = result.find_element(By.CSS_SELECTOR, 'img.s-image').get_attribute('src')
                except Exception as e:
                    print(f"Error finding image for a result: {e}")
                    image_url = None

                amazon_results.append({
                    'url': product_url,
                    'title': product_title,
                    'image_url': image_url
                })
            except Exception as e:
                # print(f"Error extracting data from a result: {e}")
                continue

        return amazon_results
    except Exception as e:
        print(f"Error occurred while searching Amazon: {e}")
        return []
    finally:
        driver.close()

# Function to scrape Walgreens promotions using Selenium
def scrape_walgreens_promotions_selenium(target_url):
    # Initialize Selenium WebDriver with the existing Chrome session
    chrome_options = Options()
    chrome_options.debugger_address = "localhost:9222"  # Connect to running Chrome session
    chrome_options.headless = False  # To see browser actions

    driver = webdriver.Chrome(options=chrome_options)

    # Open a new tab with Walgreens URL
    driver.execute_script(f"window.open('{target_url}', '_blank');")
    driver.switch_to.window(driver.window_handles[-1])
    print(f"Opened new Walgreens tab: {driver.current_url}")

    # Wait for the product container to load
    try:
        wait = WebDriverWait(driver, 30)  # Wait for up to 30 seconds
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'ul.product-container')))
        
        # Get the page content after the JavaScript has loaded
        content = driver.page_source
        soup = BeautifulSoup(content, 'html.parser')

        # Locate the product list container
        products_list = soup.find('ul', class_='product-container')

        # Check if products_list is found
        if not products_list:
            print("No products found on the page.")
            return []

        products = products_list.find_all('li', class_='item owned-brands')
        walgreens_products = []

        print(f"Found {len(products)} products on the page.")

        for product in products:
            try:
                # Extract the product URL to navigate to the product details page
                product_url = 'https://www.walgreens.com' + product.find('a', href=True)['href']

                # Check if product URL is already in the database
                session = SessionLocal()
                existing_product = session.query(Product).filter_by(product_url=product_url).first()
                session.close()

                # Retry logic for loading the product details page
                retries = 3
                while retries > 0:
                    try:
                        # Open the product details page
                        driver.get(product_url)
                        wait = WebDriverWait(driver, 15)  # Set a 15-second timeout
                        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'ul#thumbnailImages')))  # Wait for thumbnail images to load
                        break  # Exit the retry loop if successful
                    except Exception as e:
                        retries -= 1
                        print(f"Timeout reached for {product_url}. Retrying... {3 - retries} attempt(s) left.")
                        if retries > 0:
                            driver.refresh()  # Refresh the page and retry
                        else:
                            print(f"Failed to load product details page after 3 attempts. Skipping {product_url}.")
                            continue  # Skip to the next product if retries are exhausted

                # Get the page content for the product details page
                product_content = driver.page_source
                product_soup = BeautifulSoup(product_content, 'html.parser')

                # Extract the full product name including the brand, title, and size from the h1 tag with id="productName"
                product_name_h1 = product_soup.find('h1', id='productName')
                title = " ".join(product_name_h1.stripped_strings) if product_name_h1 else "No title found"

                # Extract prices (regular price and sale price) and clean the result
                regular_price_div = product_soup.find('div', id='regular-price-wag-hn-lt-bold')
                regular_price = regular_price_div.text.strip().replace('old price', '').strip() if regular_price_div else "No regular price"

                sales_price_div = product_soup.find('span', id='sales-price')
                sales_price = sales_price_div.text.strip().replace('Sale price', '').strip() if sales_price_div else "No sales price"

                if existing_product:
                    if existing_product.last_seen_price == sales_price:
                        print(f"Product with URL {product_url} already exists and the price is the same seting as in stock.")
                        existing_product.in_stock = True
                        session = SessionLocal()
                        session.add(existing_product)
                        session.commit()
                        session.close()
                        continue
                    print(f"Product with URL {product_url} already exists, updating the price.")
                    existing_product.last_seen_price = sales_price
                    existing_product.in_stock = True
                    session = SessionLocal()
                    session.add(existing_product)
                    session.commit()
                    session.close()
                    continue

                # Extract the image from the div you mentioned
                image_urls = []
                image_div = product_soup.find('div', style=lambda s: 'background-image' in s if s else False)
                if image_div:
                    # Extract background image URL from style attribute
                    style = image_div['style']
                    image_url = style.split('url(')[-1].split(')')[0].replace('"', '').replace("'", "")
                    if image_url.startswith("//"):
                        image_url = f"https:{image_url}"
                    image_urls.append(image_url)

                # Also extract images from the thumbnail carousel as a fallback
                thumbnails = product_soup.find('ul', id='thumbnailImages')
                if thumbnails:
                    # Loop through each <li> element that contains the <img> and extract the image URLs
                    for li in thumbnails.find_all('li'):
                        img_tag = li.find('img')
                        if img_tag and img_tag.get('src'):
                            # Add https: to the image URL as it seems to be incomplete
                            image_url = f"https:{img_tag['src']}"
                            image_urls.append(image_url)

                walgreens_product={
                    'title': title,
                    'price': sales_price,
                    'image_urls': image_urls,
                    'product_url': product_url,
                    'source': 'walgreens'
                }

                print(f"Product: {title}\nPrice: {sales_price}\nProduct URL: {product_url}")

                walgreens_products.append(walgreens_product)

                # Perform Amazon search and return the first 10 results
                amazon_results = search_amazon_with_selenium(walgreens_product)
                matching_indexes = find_matching_amazon_images(walgreens_product, amazon_results)
                matching_amazon = []

                for matching_index in matching_indexes:
                    try:
                        # print(f"Matching Amazon url: {amazon_results[matching_index-1]['url']}")
                        matching_amazon.append(amazon_results[matching_index-1])
                        print(f"Amazon URL: {amazon_results[matching_index-1]['url']}")
                    except IndexError:
                        print(f"Index {matching_index} is out of range.")
                        pass

                insert_data_to_db(walgreens_product, matching_amazon)
                print(f"Inserted product: {title}")
                print (10*'-')
                time.sleep(3)
            except Exception as e:
                print(f"Error occurred while processing a product: {e}")
                continue

        return walgreens_products

    except Exception as e:
        print(f"Error occurred while scraping Walgreens: {e}")
        pass
    finally:
        # Close only the current tab
        driver.close()


# Function to scrape CVS promotions from the product list without opening each product
def scrape_cvs_promotions_selenium(target_url):
    # Selectively encode the problematic parts of the URL
    encoded_url = target_url.replace("'", "%27")

    # Initialize Selenium WebDriver with the existing Chrome session
    chrome_options = Options()
    chrome_options.debugger_address = "localhost:9222"  # Connect to running Chrome session
    chrome_options.headless = False  # To see browser actions

    driver = webdriver.Chrome(options=chrome_options)

    # Open a new tab with the encoded CVS URL
    driver.execute_script(f"window.open('{encoded_url}', '_blank');")
    driver.switch_to.window(driver.window_handles[-1])
    print(f"Opened new CVS tab: {driver.current_url}")

    # Wait for the product container to load
    try:
        wait = WebDriverWait(driver, 30)  # Wait for up to 30 seconds
        # CSS selector for the product container
        product_list_selector = (
            "#root > div > div > div > div.css-1dbjc4n.r-13awgt0.r-1mlwlqe.r-1wgg2b2.r-13qz1uu > div > div:nth-child(1) > div > div > div > main > div > div > div.css-1dbjc4n.r-n2h5ot.r-bnwqim.r-13qz1uu > div > div > div > div.css-1dbjc4n.r-13awgt0.r-1mlwlqe > div > div > div"
        )
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, product_list_selector)))

        # Get the page content after the JavaScript has loaded
        content = driver.page_source
        soup = BeautifulSoup(content, 'html.parser')

        # Locate the product list container
        products_list = soup.select_one(product_list_selector)

        # Check if products_list is found
        if not products_list:
            print("No products found on the page.")
            return []

        # Locate all the product elements using the specific class you provided
        products = products_list.find_all('div', class_='css-1dbjc4n r-18u37iz r-tzz3ar')
        cvs_products = []

        print(f"Found {len(products)} products on the page.")

        for product in products:
            try:
                # Extract product title from the section you specified
                title_div = product.find('div', class_='css-901oao css-cens5h r-b0vftf r-1xaesmv r-ubezar r-majxgm r-29m4ib r-rjixqe r-1bymd8e r-fdjqy7 r-13qz1uu')
                title = title_div.text.strip() if title_div else "No title found"

                # Extract product image URL
                img_tag = product.find('img', class_='PLP-tile-image')
                image_url = img_tag['src'] if img_tag else ''
                if image_url.startswith("//"):
                    image_url = f"https:{image_url}"

                # Extract product price from the <div> containing the price
                price_div = product.find('div', class_='css-901oao', attrs={'aria-label': lambda x: x and 'Price' in x})
                price = price_div.text.strip() if price_div else "No price found"

                # Extract the product URL from the <a> tag
                product_link_tag = product.find('a', href=True)
                product_url = 'https://www.cvs.com' + product_link_tag['href'] if product_link_tag else "No URL found"

                # Check if product URL is already in the database
                session = SessionLocal()
                existing_product = session.query(Product).filter_by(product_url=product_url).first()
                session.close()        

                if existing_product:
                    if existing_product.last_seen_price == price:
                        print(f"Product with URL {product_url} already exists and the price is the same seting as in stock.")
                        existing_product.in_stock = True
                        session = SessionLocal()
                        session.add(existing_product)
                        session.commit()
                        session.close()
                        continue
                    print(f"Product with URL {product_url} already exists, updating the price.")
                    existing_product.last_seen_price = price
                    existing_product.in_stock = True
                    session = SessionLocal()
                    session.add(existing_product)
                    session.commit()
                    session.close()
                    continue


                cvs_product = {
                    'title': title,
                    'price': price,
                    'image_urls': [image_url],
                    'product_url': product_url,
                    'source': 'cvs'
                }

                print(f"Product: {title}\nPrice: {price}\nImage URL: {image_url}\nProduct URL: {product_url}")
                cvs_products.append(cvs_product)

                # Perform Amazon search and return the first 10 results
                amazon_results = search_amazon_with_selenium(cvs_product)
                matching_indexes = find_matching_amazon_images(cvs_product, amazon_results)
                matching_amazon = []

                for matching_index in matching_indexes:
                    try:
                        # print(f"Matching Amazon url: {amazon_results[matching_index-1]['url']}")
                        matching_amazon.append(amazon_results[matching_index-1])
                        print(f"Amazon URL: {amazon_results[matching_index-1]['url']}")
                    except IndexError:
                        print(f"Index {matching_index} is out of range.")
                        pass

                insert_data_to_db(cvs_product, matching_amazon)
                print (10*'-')

            except Exception as e:
                print(f"Error occurred while processing a product: {e}")
                continue

        time.sleep(5)
        return cvs_products

    except Exception as e:
        print(f"Error occurred while scraping CVS: {e}")
        pass
    finally:
        # Close only the current tab
        driver.close()



# Function to insert the product and match data into the database
def insert_data_to_db(product_data, amazon_data):
    session = SessionLocal()
    try:
        # Check if the Walgreens product already exists by URL
        existing_product = session.query(Product).filter_by(product_url=product_data['product_url']).first()

        if existing_product:
            product_id = existing_product.id
        else:
            # Insert Walgreens Product if it doesn't exist
            product = Product(
                title=product_data['title'],
                image_urls=product_data['image_urls'],
                product_url=product_data['product_url'],
                source=product_data['source'],
                last_seen_price=product_data['price'],
                in_stock=True
            )
            session.add(product)
            session.commit()
            product_id = product.id

        # Insert Amazon products and matches
        for amazon in amazon_data:
            asin = extract_asin(amazon['url'])  # Extract the ASIN from the Amazon URL
            
            # Check if the Amazon product with this ASIN already exists
            existing_amazon_product = session.query(AmazonProduct).filter_by(asin=asin).first()

            if existing_amazon_product:
                amazon_product_id = existing_amazon_product.id
            else:
                # Insert Amazon product if it doesn't exist
                amazon_product = AmazonProduct(
                    asin=asin,
                    title=amazon['title'],
                    product_url=amazon['url'],
                    image_url=amazon['image_url']
                )
                session.add(amazon_product)
                session.commit()
                amazon_product_id = amazon_product.id

            # Check if the match between product and Amazon product already exists
            existing_match = session.query(ProductMatch).filter_by(product_id=product_id, amazon_product_id=amazon_product_id).first()

            if not existing_match:
                # Create and insert the match if it doesn't exist
                product_match = ProductMatch(product_id=product_id, amazon_product_id=amazon_product_id)
                session.add(product_match)
                session.commit()

    except Exception as e:
        session.rollback()
        print(f"Error inserting data into database: {e}")
    finally:
        session.close()

# Function to find matching Amazon product images using OpenAI API
def find_matching_amazon_images(product, amazon_results):
    # Extract the first image URL from the Walgreens product
    image_urls = product.get('image_urls', [])
    title = product.get('title')
    if not image_urls:
        print("No image URLs found for Walgreens product.")
        return []

    first_image_url = image_urls[0]  # Take the first image URL

    # Extract Amazon image URLs from the amazon_results
    amazon_image_urls = [result['image_url'] for result in amazon_results if 'image_url' in result]

    # Construct the prompt
    prompt = f"In the first image i have a product, check if the produt is present in any othe other images make sure is the same product with teh same colors and details. return just an array and nothing else with the list of integer indexes of images that match the first image."


    # amazon_titles = [result['title'] for result in amazon_results if 'title' in result]

    # prompt=(f"I have the product with title {title} and following is a list of titles one by line, please check if my main title matches any of the other titles and return just and array with the indexes of the titles that match the main title.")

    # for amazon_title in amazon_titles:
    #     prompt += f"\n{amazon_title}"

    # Prepare the message
    message = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": first_image_url}}
            ]
        }
    ]

    # Append each Amazon image URL to the message
    for image_url in amazon_image_urls:
        message[0]['content'].append({
            "type": "image_url",
            "image_url": {"url": image_url}
        })

    # print(f"message: {message}")

    # Send the request to the OpenAI API
    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=message,
        max_tokens=300,
    )

    message_content = response.choices[0].message.content.strip()

    # Remove any backticks or unnecessary formatting around the JSON
    if message_content.startswith("```") and message_content.endswith("```"):
        message_content = message_content[3:-3].strip()

    # If there's a "json" label, remove it
    if message_content.startswith("json"):
        message_content = message_content[4:].strip()

    # Parse the JSON response from the assistant
    try:
        matching_indexes = json.loads(message_content)
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON: {e}")
        matching_indexes = []

    # print(matching_indexes)

    return matching_indexes


# Function to retrieve product URLs with their related Amazon product URLs
def get_products_with_amazon_urls(session):
    # Query products that are in stock and have at least one Amazon product match
    products_with_amazon = session.query(Product).filter(
        Product.in_stock == True,  # Filter by in_stock = True
        Product.product_matches.any(and_(
            ProductMatch.amazon_product.has(AmazonProduct.amazon_buy_box_count < 45),
            ProductMatch.amazon_product.has(AmazonProduct.current_sellers > 2)
        ))  # Filter AmazonProducts with amazon_buy_box_count < 45 and current_sellers > 2
    ).options(
        joinedload(Product.product_matches).joinedload(ProductMatch.amazon_product)
    ).order_by(desc(Product.id)).all()

    result = []

    # Build the result list with the product URL and the associated Amazon URLs
    for product in products_with_amazon:
        product_data = {
            'product_url': product.product_url,
            'amazon_urls': [
                match.amazon_product.product_url
                for match in product.product_matches
                if match.amazon_product and match.amazon_product.amazon_buy_box_count < 45 and match.amazon_product.current_sellers > 2
            ]
        }
        result.append(product_data)

    return result

# Function to scrape Sam's Club promotions using Selenium
def scrape_samsclub_promotions_selenium(target_url):
    # Initialize Selenium WebDriver with the existing Chrome session
    chrome_options = Options()
    chrome_options.debugger_address = "localhost:9222"  # Connect to running Chrome session
    chrome_options.headless = False  # To see browser actions

    driver = webdriver.Chrome(options=chrome_options)

    # Open a new tab with Sam's Club URL
    driver.execute_script(f"window.open('{target_url}', '_blank');")
    driver.switch_to.window(driver.window_handles[-1])
    print(f"Opened new Sam's Club tab: {driver.current_url}")

    # Wait for the product container to load
    try:
        wait = WebDriverWait(driver, 30)  # Wait for up to 30 seconds
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div.sc-plp-cards.sc-plp-cards-grid')))  # Adjust for Sam's Club


        # Get the page content after the JavaScript has loaded
        content = driver.page_source
        soup = BeautifulSoup(content, 'html.parser')

        # Locate the product list container
        products_list = soup.find_all('div', class_='sc-product-card')  # Adjust for Sam's Club
        print(products_list)
        exit()

        # Check if products_list is found
        if not products_list:
            print("No products found on the page.")
            return []

        walgreens_products = []

        print(f"Found {len(products_list)} products on the page.")

        for product in products_list:
            try:
                # Extract the product URL to navigate to the product details page
                product_url = 'https://www.samsclub.com' + product.find('a', href=True)['href']

                # Check if product URL is already in the database
                session = SessionLocal()
                existing_product = session.query(Product).filter_by(product_url=product_url).first()
                session.close()

                # Retry logic for loading the product details page
                retries = 3
                while retries > 0:
                    try:
                        # Open the product details page
                        driver.get(product_url)
                        wait = WebDriverWait(driver, 15)  # Set a 15-second timeout
                        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div.prod-price')))  # Wait for price element
                        break  # Exit the retry loop if successful
                    except Exception as e:
                        retries -= 1
                        print(f"Timeout reached for {product_url}. Retrying... {3 - retries} attempt(s) left.")
                        if retries > 0:
                            driver.refresh()  # Refresh the page and retry
                        else:
                            print(f"Failed to load product details page after 3 attempts. Skipping {product_url}.")
                            continue  # Skip to the next product if retries are exhausted

                # Get the page content for the product details page
                product_content = driver.page_source
                product_soup = BeautifulSoup(product_content, 'html.parser')

                # Extract the full product name including the brand, title, and size from the h1 tag with class="sc-product-title"
                product_name_h1 = product_soup.find('h1', class_='sc-product-title')
                title = " ".join(product_name_h1.stripped_strings) if product_name_h1 else "No title found"

                # Extract prices (regular price and sale price) and clean the result
                regular_price_div = product_soup.find('span', class_='prod-price-old')
                regular_price = regular_price_div.text.strip().replace('old price', '').strip() if regular_price_div else "No regular price"

                sales_price_div = product_soup.find('span', class_='prod-price-primary')
                sales_price = sales_price_div.text.strip().replace('Sale price', '').strip() if sales_price_div else "No sales price"

                if existing_product:
                    if existing_product.last_seen_price == sales_price:
                        print(f"Product with URL {product_url} already exists and the price is the same seting as in stock.")
                        existing_product.in_stock = True
                        session = SessionLocal()
                        session.add(existing_product)
                        session.commit()
                        session.close()
                        continue
                    print(f"Product with URL {product_url} already exists, updating the price.")
                    existing_product.last_seen_price = sales_price
                    existing_product.in_stock = True
                    session = SessionLocal()
                    session.add(existing_product)
                    session.commit()
                    session.close()
                    continue

                # Extract the image from the div
                image_urls = []
                image_div = product_soup.find('img', class_='sc-product-image')
                if image_div:
                    # Extract image URL from src attribute
                    image_url = image_div['src']
                    if image_url.startswith("//"):
                        image_url = f"https:{image_url}"
                    image_urls.append(image_url)

                walgreens_product = {
                    'title': title,
                    'price': sales_price,
                    'image_urls': image_urls,
                    'product_url': product_url,
                    'source': 'Sams Club'
                }

                print(f"Product: {title}\nPrice: {sales_price}\nProduct URL: {product_url}")

                walgreens_products.append(walgreens_product)

                # Perform Amazon search and return the first 10 results
                amazon_results = search_amazon_with_selenium(walgreens_product)
                matching_indexes = find_matching_amazon_images(walgreens_product, amazon_results)
                matching_amazon = []

                for matching_index in matching_indexes:
                    try:
                        # print(f"Matching Amazon url: {amazon_results[matching_index-1]['url']}")
                        matching_amazon.append(amazon_results[matching_index-1])
                        print(f"Amazon URL: {amazon_results[matching_index-1]['url']}")
                    except IndexError:
                        print(f"Index {matching_index} is out of range.")
                        pass

                insert_data_to_db(walgreens_product, matching_amazon)
                print(f"Inserted product: {title}")
                print(10 * '-')
                time.sleep(3)
            except Exception as e:
                print(f"Error occurred while processing a product: {e}")
                continue

        return walgreens_products

    except Exception as e:
        print(f"Error occurred while scraping Sam's Club: {e}")
        pass
    finally:
        # Close only the current tab
        driver.close()


# Main function to run the script
async def main():
    ### Print report
    # session = SessionLocal()
    # products_with_amazon_urls = get_products_with_amazon_urls(session)

    # for item in products_with_amazon_urls:
    #     print(f"Product URL: {item['product_url']}")
    #     print(f"Amazon URLs: {', '.join(item['amazon_urls'])}")
    #     print(10*'-')

    ### Scrape walgreens
    # target_url_list = [
    #     "https://www.walgreens.com/store/store/category/productlist.jsp?ban=dl_dlDMI_HeroREFRESH909_9082024_FlashSale_Ex20PO35&webExc=true&N=4294896499%2B1000023&Eon=4294896499&inStockOnly=true",
    #     "https://www.walgreens.com/store/store/category/productlist.jsp?ban=dl_dlDMI_HeroREFRESH909_9082024_FlashSale_Ex20PO35&webExc=true&N=4294896499%2B1000023&Eon=4294896499&inStockOnly=true&No=72",
    #     "https://www.walgreens.com/store/store/category/productlist.jsp?ban=dl_dlDMI_HeroREFRESH909_9082024_FlashSale_Ex20PO35&webExc=true&N=4294896499%2B1000023&Eon=4294896499&inStockOnly=true&No=144",
    #     "https://www.walgreens.com/store/store/category/productlist.jsp?ban=dl_dlDMI_HeroREFRESH909_9082024_FlashSale_Ex20PO35&webExc=true&N=4294896499%2B1000023&Eon=4294896499&inStockOnly=true&No=216",
    #     "https://www.walgreens.com/store/store/category/productlist.jsp?ban=dl_dlDMI_HeroREFRESH909_9082024_FlashSale_Ex20PO35&webExc=true&N=4294896499%2B1000023&Eon=4294896499&inStockOnly=true&No=288",
    #     "https://www.walgreens.com/store/store/category/productlist.jsp?ban=dl_dlDMI_HeroREFRESH909_9082024_FlashSale_Ex20PO35&webExc=true&N=4294896499%2B1000023&Eon=4294896499&inStockOnly=true&No=360",
    #     "https://www.walgreens.com/store/store/category/productlist.jsp?ban=dl_dlDMI_HeroREFRESH909_9082024_FlashSale_Ex20PO35&webExc=true&N=4294896499%2B1000023&Eon=4294896499&inStockOnly=true&No=432",
    #     "https://www.walgreens.com/store/store/category/productlist.jsp?ban=dl_dlDMI_HeroREFRESH909_9082024_FlashSale_Ex20PO35&webExc=true&N=4294896499%2B1000023&Eon=4294896499&inStockOnly=true&No=504",
    #     "https://www.walgreens.com/store/store/category/productlist.jsp?ban=dl_dlDMI_HeroREFRESH909_9082024_FlashSale_Ex20PO35&webExc=true&N=4294896499%2B1000023&Eon=4294896499&inStockOnly=true&No=576",
    # ]
    # for target_url in target_url_list:
    #     scrape_walgreens_promotions_selenium(target_url)

    ### Scrape samsclub
    # target_url_list = [
    #     "https://www.samsclub.com/savings?altQuery=1585&xid=plp_popcat_Health%20&%20Beauty_6",
    # ]
    # for target_url in target_url_list:
    #     scrape_samsclub_promotions_selenium(target_url)

    ### Scrape cvs
    target_url_list = [
        "https://www.cvs.com/shop/merch/weekly-bogo-vitamins/q/Buy_1%2C_Get_1_Free/Buy_1%2C_Get_1_50%25_Off/Nature_Made/Nature's_Bounty/Nature's_Truth/Sundown_Naturals/Natures_Bounty/Natrol/Nature's_Way/Citracal/Nervive/Osteo_Bi-Flex/One_A_Day/Qunol/Natures_Way/Vicks_ZzzQuil/Airborne/Alive!/HumanN/Digestive_Advantage/Himalaya/Phillips'/Ester-C/Lisa_Frank/MiraLAX/Kappa_Books/Napz/Pure_ZZZs/Zarbee's/Vicks/Orgain/Tablets%2C_Capsules_%26_Caplets/Softgels/Chewables/Vegetarian_Tablets_%26_Capsules/Powder/Liquid/Oil/Lozenges/Dissolving_%2F_Meltaway_Tablets/prprbrbrbrbrbrbrbrbrbrbrbrbrbrbrbrbrbrbrbrbrbrbrbrbrbrbrbrbrbrfmfmfmfmfmfmfmfmfm?widgetID=rj0r73j6&mc=cat2",
    ]
    for target_url in target_url_list:
        scrape_cvs_promotions_selenium(target_url)

    ### Set buy box and sellers
    # # OpenAI API Key (if still needed elsewhere in the script)
    # openai.api_key = os.getenv("OPENAI_API_KEY")

    # keepa_api_key = os.getenv('KEEPA_API')
    # if not keepa_api_key:
    #     logger.error("Keepa API key not found in environment variables. Set the 'KEEPA_API' environment variable.")
    #     raise ValueError("Keepa API key not found in environment variables. Set the 'KEEPA_API' environment variable.")

    # logger.debug("Initializing Keepa API with the provided key.")
    # keepa_api = keepa.Keepa(keepa_api_key)
    # session = SessionLocal()
    # analyze_and_update_products(session)
    

if __name__ == "__main__":
    asyncio.run(main())
