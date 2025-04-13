// Attachment generation service using wkhtmltopdf/wkhtmltoimage
// Only using HTML-to-image-to-PDF workflow

const fs = require('fs');
const path = require('path');
const { v4: uuidv4 } = require('uuid');
const { exec } = require('child_process');
const logger = require('./logger');
const { processDynamicTags, Semaphore } = require('./utils');

// Create a dedicated temp directory
const tempDir = path.join(process.cwd(), 'temp');
if (!fs.existsSync(tempDir)) {
  fs.mkdirSync(tempDir, { recursive: true });
}

// Semaphore to limit concurrent wkhtmltopdf processes
const wkhtmlSemaphore = new Semaphore(5);

// Function to modify HTML to handle external resources locally
function prepareHtmlForRendering(html) {
  const enhancedHtml = `
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body>
  ${html}
</body>
</html>`;

  return enhancedHtml;
}

// Function to generate image from HTML using wkhtmltoimage
async function generateImageFromHtml(html, format = 'png') {
  await wkhtmlSemaphore.acquire();
  
  return new Promise((resolve, reject) => {
    try {
      const processedHtml = prepareHtmlForRendering(html);
      
      const tempHtmlPath = path.join(tempDir, `input-${uuidv4()}.html`);
      const tempImgPath = path.join(tempDir, `output-${uuidv4()}.${format}`);
      
      fs.writeFileSync(tempHtmlPath, processedHtml, 'utf8');
      
      const cmd = `wkhtmltoimage --quiet --enable-local-file-access --quality 100 --width 800 --enable-javascript --load-error-handling ignore "file://${tempHtmlPath}" "${tempImgPath}"`;
      
      logger.info(`Executing wkhtmltoimage command`);
      
      exec(cmd, (error, stdout, stderr) => {
        try {
          if (fs.existsSync(tempHtmlPath)) {
            fs.unlinkSync(tempHtmlPath);
          }
          
          if (fs.existsSync(tempImgPath) && fs.statSync(tempImgPath).size > 0) {
            try {
              const imgContent = fs.readFileSync(tempImgPath);
              fs.unlinkSync(tempImgPath);
              
              resolve({
                content: imgContent,
                path: tempImgPath
              });
              return;
            } catch (readError) {
              logger.error(`Error reading generated image: ${readError.message}`);
            }
          }
          
          if (error) {
            logger.error(`wkhtmltoimage error: ${error.message}`);
            if (stderr) logger.error(`stderr: ${stderr}`);
            reject(error);
            return;
          }
          
          reject(new Error("Failed to generate image from HTML"));
        } catch (cleanupError) {
          logger.error(`Error during cleanup: ${cleanupError.message}`);
          reject(cleanupError);
        } finally {
          wkhtmlSemaphore.release();
        }
      });
    } catch (error) {
      wkhtmlSemaphore.release();
      logger.error(`Error in generateImageFromHtml: ${error.message}`);
      reject(error);
    }
  });
}

// Function to generate PDF from an image - works with base64 to avoid file:// issues
async function generatePdfFromImage(imagePath, imageContent) {
  await wkhtmlSemaphore.acquire();
  
  return new Promise((resolve, reject) => {
    try {
      let imgContent = imageContent;
      
      if (!imgContent && imagePath) {
        imgContent = fs.readFileSync(imagePath);
      } else if (!imagePath && !imageContent) {
        reject(new Error("Either imagePath or imageContent must be provided"));
        wkhtmlSemaphore.release();
        return;
      }
      
      // Convert to base64 to avoid file:// protocol issues
      const imgBase64 = imgContent.toString('base64');
      const mimeType = imagePath && imagePath.toLowerCase().endsWith('.jpg') ? 'image/jpeg' : 'image/png';
      
      const tempHtmlPath = path.join(tempDir, `img-html-${uuidv4()}.html`);
      const html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
</head>
<body>
  <img src="data:${mimeType};base64,${imgBase64}" width="1200" alt="Generated Content">
</body>
</html>`;
      
      fs.writeFileSync(tempHtmlPath, html, 'utf8');
      
      const tempPdfPath = path.join(tempDir, `img-pdf-${uuidv4()}.pdf`);
      
      const cmd = `wkhtmltopdf --quiet --enable-local-file-access --encoding UTF-8 --minimum-font-size 12 --dpi 300 --margin-top 0 --margin-bottom 0 --margin-left 0 --margin-right 0 --page-width 210mm --page-height 370mm --zoom 1.5 --load-error-handling ignore "file://${tempHtmlPath}" "${tempPdfPath}"`;
      
      logger.info(`Executing wkhtmltopdf command for image-to-pdf`);
      
      exec(cmd, (error, stdout, stderr) => {
        try {
          if (fs.existsSync(tempHtmlPath)) {
            fs.unlinkSync(tempHtmlPath);
          }
          
          if (fs.existsSync(tempPdfPath) && fs.statSync(tempPdfPath).size > 0) {
            try {
              const pdfContent = fs.readFileSync(tempPdfPath);
              fs.unlinkSync(tempPdfPath);
              
              resolve(pdfContent);
              return;
            } catch (readError) {
              logger.error(`Error reading generated PDF: ${readError.message}`);
            }
          }
          
          if (error) {
            logger.error(`Error converting image to PDF: ${error.message}`);
            if (stderr) logger.error(`stderr: ${stderr}`);
            reject(error);
            return;
          }
          
          reject(new Error("Failed to generate PDF from image"));
        } catch (cleanupError) {
          logger.error(`Error during cleanup: ${cleanupError.message}`);
          reject(cleanupError);
        } finally {
          wkhtmlSemaphore.release();
        }
      });
    } catch (error) {
      wkhtmlSemaphore.release();
      logger.error(`Error in generatePdfFromImage: ${error.message}`);
      reject(error);
    }
  });
}

// Create an attachment (PDF, Image, Word) from HTML content
async function createAttachment(attachmentType, attachmentHtml, emailInfo, customTagValues) {
  if (attachmentType === 'none' || !attachmentHtml) {
    return null;
  }
  
  try {
    const processedHtml = processDynamicTags(attachmentHtml, emailInfo, customTagValues);
    
    if (attachmentType === 'pdf') {
      logger.info('Generating image from HTML');
      const imageResult = await generateImageFromHtml(processedHtml, 'png');
      
      logger.info('Generating PDF from image');
      const pdfContent = await generatePdfFromImage(null, imageResult.content);
      
      return { 
        content: pdfContent, 
        contentType: 'application/pdf',
        extension: '.pdf'
      };
    } 
    else if (attachmentType === 'jpeg' || attachmentType === 'png') {
      logger.info(`Generating ${attachmentType} image from HTML`);
      const imageResult = await generateImageFromHtml(processedHtml, attachmentType);
      
      return { 
        content: imageResult.content, 
        contentType: `image/${attachmentType}`,
        extension: attachmentType === 'jpeg' ? '.jpg' : '.png'
      };
    } 
    else if (attachmentType === 'word') {
      return { 
        content: Buffer.from(processedHtml, 'utf8'), 
        contentType: 'application/msword',
        extension: '.doc'
      };
    }
    
    return null;
  } catch (error) {
    logger.error(`Error creating attachment: ${error.message}`);
    return null;
  }
}

// Check if wkhtmltopdf is installed
async function checkWkhtmlInstallation() {
  return new Promise((resolve) => {
    exec('wkhtmltopdf --version', (error, stdout, stderr) => {
      if (error) {
        logger.error(`wkhtmltopdf is not installed or not in PATH: ${error.message}`);
        logger.error('Please install wkhtmltopdf: sudo apt-get install -y wkhtmltopdf');
        resolve(false);
      } else {
        logger.info(`wkhtmltopdf version: ${stdout.trim()}`);
        resolve(true);
      }
    });
    
    exec('wkhtmltoimage --version', (error, stdout, stderr) => {
      if (error) {
        logger.error(`wkhtmltoimage is not installed or not in PATH: ${error.message}`);
        logger.error('Please install wkhtmltoimage: sudo apt-get install -y wkhtmltopdf');
      } else {
        logger.info(`wkhtmltoimage version: ${stdout.trim()}`);
      }
    });
  });
}

// Initialize - check for wkhtmltopdf installation
checkWkhtmlInstallation();

// Export functions
module.exports = {
  createAttachment
};