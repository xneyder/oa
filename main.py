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

# Import the models and session setup from the previous explanation
from app.models import Product, AmazonProduct, ProductMatch
from app.db import SessionLocal

# OpenAI API Key (if still needed elsewhere in the script)
openai.api_key = os.getenv("OPENAI_API_KEY")


def extract_asin(url):
    match = re.search(r'/([A-Z0-9]{10})(?:[/?]|$)', url)
    return match.group(1) if match else None

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
def scrape_walgreens_promotions_selenium():
    # Initialize Selenium WebDriver with the existing Chrome session
    chrome_options = Options()
    chrome_options.debugger_address = "localhost:9222"  # Connect to running Chrome session
    chrome_options.headless = False  # To see browser actions

    driver = webdriver.Chrome(options=chrome_options)

    # Open a new tab with Walgreens URL
    driver.execute_script("window.open('https://www.walgreens.com/search/results.jsp?Ntt=Clearance&ban=dl_dl_Nav_9082024_', '_blank');")
    # driver.execute_script("window.open('https://www.walgreens.com/search/results.jsp?Ntt=%20BD%20Alcohol%20Swabs-0%20', '_blank');")
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

        products = products_list.find_all('li', class_='item owned-brands')[:10]  # Limit to 10 products
        walgreens_products = []

        for product in products:
            # Extract the product URL to navigate to the product details page
            product_url = 'https://www.walgreens.com' + product.find('a', href=True)['href']

            # Check if product URL is already in the database
            session = SessionLocal()
            existing_product = session.query(Product).filter_by(product_url=product_url).first()
            session.close()

            if existing_product:
                print(f"Product with URL {product_url} already exists, skipping.")
                continue

            # Open the product details page
            driver.get(product_url)
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'ul#thumbnailImages')))  # Wait for thumbnail images to load

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
                'regular_price': regular_price,
                'sales_price': sales_price,
                'image_urls': image_urls,  # Store as a list of image URLs
                'product_url': product_url
            }

            walgreens_products.append(walgreens_product)

            # Perform Amazon search and return the first 10 results
            amazon_results = search_amazon_with_selenium(walgreens_product)
            matching_indexes = find_matching_amazon_images(walgreens_product, amazon_results)
            matching_amazon = []

            for matching_index in matching_indexes:
                try:
                    # print(f"Matching Amazon url: {amazon_results[matching_index-1]['url']}")
                    matching_amazon.append(amazon_results[matching_index-1])
                except IndexError:
                    print(f"Index {matching_index} is out of range.")
                    pass

            insert_data_to_db(walgreens_product, matching_amazon)
            print (10*'-')

        return walgreens_products

    except Exception as e:
        print(f"Error occurred while scraping Walgreens: {e}")
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
                source='walgreens'
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
        model="gpt-4o",
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


# Main function to run the script
async def main():
    scrape_walgreens_promotions_selenium()

    # for product in products:
    #     print(f"Checking product: {product}")
        
    #     # Perform Amazon search and return the first 10 results
    #     amazon_results = search_amazon_with_selenium(product)
    #     matching_indexes = find_matching_amazon_images(product, amazon_results)
    #     matching_amazon = []

    #     for matching_index in matching_indexes:
    #         try:
    #             print(f"Matching Amazon url: {amazon_results[matching_index-1]['url']}")
    #             matching_amazon.append(amazon_results[matching_index-1])
    #         except IndexError:
    #             print(f"Index {matching_index} is out of range.")
    #             pass

    #     insert_data_to_db(product, matching_amazon)
    #     print (10*'-')


if __name__ == "__main__":
    asyncio.run(main())
