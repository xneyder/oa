(async function () {
    // Ensure the script runs after the page has loaded
    if (document.readyState !== 'complete') {
        await new Promise((resolve) => window.addEventListener('load', resolve));
    }

    console.log('CVS Scraper Extension: Starting processing...');
    
    await scrapeCvsPromotions();
    
    async function scrapeCvsPromotions() {
        try {
            await waitForElement('div.css-1dbjc4n.r-18u37iz.r-tzz3ar', 10000);
            
            const products = document.querySelectorAll('div.css-1dbjc4n.r-18u37iz.r-tzz3ar');
            console.log(`Found ${products.length} products on the page.`);
            
            // Process only the first product for debugging
            const product = products[0];
            if (product) {
                try {
                    const title = extractTitle(product);
                    const imageUrl = extractImageUrl(product);
                    const price = extractPrice(product);
                    const productUrl = extractProductUrl(product);

                    console.log(`Product: ${title}\nPrice: ${price}\nImage URL: ${imageUrl}\nProduct URL: ${productUrl}`);
                    
                    const cvsProduct = {
                        title: title,
                        price: price,
                        image_urls: [imageUrl],
                        product_url: productUrl,
                        source: 'cvs'
                    };

                    // Send a message to the background script to search Amazon
                    const amazonResults = await searchAmazon(cvsProduct);
                    console.log(amazonResults);

                    console.log('----------');
                } catch (err) {
                    console.error('Error processing the product:', err);
                }
            }
            
            console.log('CVS Scraper Extension: Processing complete.');
        } catch (err) {
            console.error('Error scraping CVS:', err);
        }
    }

    async function searchAmazon(product) {
        return new Promise((resolve, reject) => {
            chrome.runtime.sendMessage(
                { action: 'searchAmazon', product },
                (response) => {
                    if (response.error) {
                        reject(response.error);
                    } else {
                        resolve(response.results);
                    }
                }
            );
        });
    }

    // Helper functions for extracting product details
    function waitForElement(selector, timeout) {
        return new Promise((resolve, reject) => {
            const start = Date.now();
            const interval = setInterval(() => {
                if (document.querySelector(selector)) {
                    clearInterval(interval);
                    resolve();
                } else if (Date.now() - start > timeout) {
                    clearInterval(interval);
                    reject(new Error('Element not found: ' + selector));
                }
            }, 100);
        });
    }

    function extractTitle(productElement) {
        const titleDiv = productElement.querySelector('div.css-901oao.css-cens5h.r-b0vftf.r-1xaesmv.r-ubezar.r-majxgm.r-29m4ib.r-rjixqe.r-1bymd8e.r-fdjqy7.r-13qz1uu');
        return titleDiv ? titleDiv.textContent.trim() : 'No title found';
    }

    function extractImageUrl(productElement) {
        const imgTag = productElement.querySelector('img.PLP-tile-image');
        let imageUrl = imgTag ? imgTag.src : '';
        if (imageUrl.startsWith('//')) {
            imageUrl = 'https:' + imageUrl;
        }
        return imageUrl;
    }

    function extractPrice(productElement) {
        const priceDiv = productElement.querySelector('div.css-901oao[aria-label*="Price"]');
        return priceDiv ? priceDiv.textContent.trim() : 'No price found';
    }

    function extractProductUrl(productElement) {
        const productLinkTag = productElement.querySelector('a[href]');
        return productLinkTag ? 'https://www.cvs.com' + productLinkTag.getAttribute('href') : 'No URL found';
    }
})();
