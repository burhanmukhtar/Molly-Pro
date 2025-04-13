// Enhanced attachment service using Puppeteer for better PDF generation
// With personalization for each recipient and memory optimization
const fs = require('fs');
const path = require('path');
const os = require('os'); // Added for memory detection
const puppeteer = require('puppeteer');
const { v4: uuidv4 } = require('uuid');
const logger = require('./logger');
const { processDynamicTags, Semaphore } = require('./utils');

// Create a dedicated temp directory
const tempDir = path.join(process.cwd(), 'temp');
if (!fs.existsSync(tempDir)) {
  fs.mkdirSync(tempDir, { recursive: true });
}

// Singleton browser instance (will be initialized on first use)
let browserInstance = null;

// Semaphore to limit concurrent PDF generation - will be adjusted based on system resources
const puppeteerSemaphore = new Semaphore(5); // Initial value, will be adjusted

// Initialize browser with memory optimization settings
async function getBrowser() {
  if (!browserInstance) {
    logger.info('Initializing Puppeteer browser instance with memory optimization');
    
    // Set a lower process limit based on available system memory
    const availableMemoryMB = Math.floor(os.totalmem() / (1024 * 1024));
    const isLowMemory = availableMemoryMB < 4096; // Less than 4GB
    
    // Calculate optimal concurrent processes
    const optimalConcurrency = Math.max(2, Math.min(5, Math.floor(os.cpus().length * 2)));
    
    // Only run 1 process if memory is low
    puppeteerSemaphore.max = isLowMemory ? 2 : optimalConcurrency;
    
    logger.info(`System memory: ${availableMemoryMB}MB, Setting puppeteer concurrency to ${puppeteerSemaphore.max}`);
    
    // Optimization flags for low memory environments
    const browserArgs = [
      '--no-sandbox', 
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-accelerated-2d-canvas',
      '--disable-gpu',
      '--js-flags=--expose-gc,--max-old-space-size=512'  // Limit memory per page
    ];
    
    // Add more aggressive memory optimization for low-memory systems
    if (isLowMemory) {
      browserArgs.push(
        '--single-process',
        '--disable-extensions',
        '--disable-component-extensions-with-background-pages',
        '--disable-default-apps',
        '--mute-audio',
        '--no-default-browser-check',
        '--no-first-run',
        '--disable-background-networking',
        '--disable-sync',
        '--disable-translate',
        '--disable-web-security',
        '--disable-features=site-per-process,TranslateUI,BlinkGenPropertyTrees'
      );
    }
    
    browserInstance = await puppeteer.launch({
      headless: 'new',
      args: browserArgs,
      ignoreHTTPSErrors: true,
      dumpio: false
    });
    
    // Set up cleanup on browser error
    browserInstance.on('disconnected', () => {
      logger.warn('Browser disconnected unexpectedly');
      browserInstance = null;
    });
  }
  return browserInstance;
}

// Add explicit page cleanup function
async function closePage(page) {
  if (!page) return;
  
  try {
    // Force JavaScript garbage collection if possible
    await page.evaluate(() => {
      if (window.gc) {
        window.gc();
      }
    }).catch(() => {});
    
    await page.close();
  } catch (error) {
    logger.error(`Error closing page: ${error.message}`);
  }
}

// Close browser instance (e.g., during shutdown)
async function closeBrowser() {
  if (browserInstance) {
    logger.info('Closing Puppeteer browser instance');
    await browserInstance.close();
    browserInstance = null;
  }
}

// Function to prepare HTML for rendering with personalization
function prepareHtmlForRendering(html, emailInfo, customTagValues) {
  // Process dynamic tags first
  const processedContent = processDynamicTags(html, emailInfo, customTagValues);
  
  const enhancedHtml = `
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body>
  ${processedContent}
</body>
</html>`;

  return enhancedHtml;
}

// Optimize image generation to use less memory
async function generateImageFromHtml(html, emailInfo, customTagValues, format = 'png') {
  await puppeteerSemaphore.acquire();
  
  let page = null;
  try {
    const browser = await getBrowser();
    page = await browser.newPage();
    
    // Set low memory limits for the page
    await page.setRequestInterception(true);
    page.on('request', (request) => {
      // Block unnecessary resources
      if (['image', 'media', 'font'].includes(request.resourceType())) {
        request.abort();
      } else {
        request.continue();
      }
    });
    
    // Limit page resources
    await page.setJavaScriptEnabled(true);
    await page.setCacheEnabled(false);
    
    // Process the HTML content with personalization
    const processedHtml = prepareHtmlForRendering(html, emailInfo, customTagValues);
    
    // Simplified variations to reduce memory usage
    const emailHash = Buffer.from(emailInfo.email || '').toString('base64').substring(0, 8);
    const variation = parseInt(emailHash.replace(/[^0-9]/g, '')) % 5; // Get a stable variation 0-4
    
    // Set viewport size with minimal variations
    await page.setViewport({ width: 800, height: 1200 });
    
    // Set content with modifications
    await page.setContent(processedHtml, { waitUntil: 'domcontentloaded' });
    
    // Add a hidden identifier
    await page.evaluate((email, variation) => {
      const div = document.createElement('div');
      div.style.display = 'none';
      div.innerHTML = `Recipient: ${email} - Variation: ${variation}`;
      document.body.appendChild(div);
    }, emailInfo.email, variation);
    
    // Create temporary image path
    const tempImgPath = path.join(tempDir, `image-${uuidv4()}.${format}`);
    
    // Generate screenshot
    await page.screenshot({
      path: tempImgPath,
      type: format,
      fullPage: true,
      quality: format === 'jpeg' ? 80 : undefined // Lower quality for JPEG to save memory
    });
    
    logger.info(`Image saved to: ${tempImgPath}`);
    
    const imageContent = fs.readFileSync(tempImgPath);
    
    // Clean up memory explicitly
    await page.evaluate(() => {
      if (window.gc) window.gc();
    }).catch(() => {});
    
    // Automatically remove temp file
    fs.unlinkSync(tempImgPath);
    
    return {
      content: imageContent,
      path: tempImgPath
    };
  } catch (error) {
    logger.error(`Error in generateImageFromHtml: ${error.message}`);
    throw error;
  } finally {
    // Enhanced page cleanup
    if (page) await closePage(page);
    puppeteerSemaphore.release();
  }
}

// Function to generate PDF from image using Puppeteer
async function generatePdfFromImage(imageContent) {
  await puppeteerSemaphore.acquire();
  
  let page = null;
  try {
    const browser = await getBrowser();
    page = await browser.newPage();
    
    // Set low memory limits for the page
    await page.setRequestInterception(true);
    page.on('request', (request) => {
      if (['image', 'media', 'font'].includes(request.resourceType()) && 
          !request.url().startsWith('data:')) {
        request.abort();
      } else {
        request.continue();
      }
    });
    
    // Convert image to base64
    const imgBase64 = imageContent.toString('base64');
    const dataUrl = `data:image/png;base64,${imgBase64}`;
    
    // Create an HTML page with the image
    const html = `
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8">
      <style>
        body, html {
          margin: 0;
          padding: 0;
          width: 100%;
          height: 100%;
        }
        img {
          max-width: 100%;
          display: block;
        }
      </style>
    </head>
    <body>
      <img src="${dataUrl}" alt="Generated Content">
    </body>
    </html>`;
    
    // Set content
    await page.setContent(html, { waitUntil: 'domcontentloaded' });
    
    // Create temporary PDF path
    const tempPdfPath = path.join(tempDir, `pdf-from-img-${uuidv4()}.pdf`);
    
    // Generate PDF
    await page.pdf({
      path: tempPdfPath,
      printBackground: true,
      margin: {
        top: '0',
        right: '0',
        bottom: '0',
        left: '0'
      }
    });
    
    logger.info(`PDF from image generated at: ${tempPdfPath}`);
    
    const pdfContent = fs.readFileSync(tempPdfPath);
    
    // Clean up memory explicitly
    await page.evaluate(() => {
      if (window.gc) window.gc();
    }).catch(() => {});
    
    // Automatically remove temp file
    fs.unlinkSync(tempPdfPath);
    
    return pdfContent;
  } catch (error) {
    logger.error(`Error in generatePdfFromImage: ${error.message}`);
    throw error;
  } finally {
    // Enhanced page cleanup
    if (page) await closePage(page);
    puppeteerSemaphore.release();
  }
}

// Create personalized attachment (PDF, Image, Word) from HTML content
async function createPersonalizedAttachment(attachmentType, attachmentHtml, emailInfo, customTagValues) {
  if (attachmentType === 'none' || !attachmentHtml) {
    return null;
  }
  
  try {
    logger.info(`Creating personalized ${attachmentType} attachment for ${emailInfo.email}`);
    
    if (attachmentType === 'pdf') {
      // Use the HTML to Image to PDF workflow
      logger.info('Generating image from HTML');
      const imageResult = await generateImageFromHtml(attachmentHtml, emailInfo, customTagValues, 'png');
      
      logger.info('Generating PDF from image');
      const pdfContent = await generatePdfFromImage(imageResult.content);
      
      return { 
        content: pdfContent, 
        contentType: 'application/pdf',
        extension: '.pdf'
      };
    } 
    else if (attachmentType === 'jpeg' || attachmentType === 'png') {
      logger.info(`Generating ${attachmentType} image from HTML`);
      const imageResult = await generateImageFromHtml(attachmentHtml, emailInfo, customTagValues, attachmentType);
      
      return { 
        content: imageResult.content, 
        contentType: `image/${attachmentType}`,
        extension: attachmentType === 'jpeg' ? '.jpg' : '.png'
      };
    } 
    else if (attachmentType === 'word') {
      // Process tags for Word document
      const processedHtml = processDynamicTags(attachmentHtml, emailInfo, customTagValues);
      
      return { 
        content: Buffer.from(processedHtml, 'utf8'), 
        contentType: 'application/msword',
        extension: '.doc'
      };
    }
    
    return null;
  } catch (error) {
    logger.error(`Error creating attachment for ${emailInfo.email}: ${error.message}`);
    return null;
  }
}

// Export functions
module.exports = {
  createPersonalizedAttachment,
  closeBrowser,
  getBrowser
};