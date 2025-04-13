// PDF Worker - Uses wkhtmltopdf/wkhtmltoimage to generate PDFs in a separate worker thread
// Only using HTML-to-image-to-PDF workflow

const { parentPort, workerData } = require('worker_threads');
const fs = require('fs');
const path = require('path');
const { exec } = require('child_process');

// Create a dedicated temp directory
const tempDir = path.join(process.cwd(), 'temp');
if (!fs.existsSync(tempDir)) {
  fs.mkdirSync(tempDir, { recursive: true });
}

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

// Generate image from HTML using wkhtmltoimage
async function generateImageFromHtml(html, format = 'png') {
  return new Promise((resolve, reject) => {
    try {
      const processedHtml = prepareHtmlForRendering(html);
      
      const tempHtmlPath = path.join(tempDir, `worker-${process.pid}-${Date.now()}.html`);
      const tempImgPath = path.join(tempDir, `worker-img-${process.pid}-${Date.now()}.${format}`);
      
      fs.writeFileSync(tempHtmlPath, processedHtml, 'utf8');
      
      const cmd = `wkhtmltoimage --quiet --enable-local-file-access --quality 100 --width 800 --enable-javascript --load-error-handling ignore "file://${tempHtmlPath}" "${tempImgPath}"`;
      
      console.log(`Worker ${process.pid}: Executing wkhtmltoimage command`);
      
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
              console.error(`Worker ${process.pid}: Error reading generated image: ${readError.message}`);
            }
          }
          
          if (error) {
            console.error(`Worker ${process.pid}: wkhtmltoimage error: ${error.message}`);
            console.error(`Worker ${process.pid}: stderr: ${stderr}`);
            reject(error);
            return;
          }
          
          reject(new Error("Failed to generate image"));
        } catch (cleanupError) {
          console.error(`Worker ${process.pid}: Error during cleanup: ${cleanupError.message}`);
          reject(cleanupError);
        }
      });
    } catch (error) {
      console.error(`Worker ${process.pid}: Error in generateImageFromHtml: ${error.message}`);
      reject(error);
    }
  });
}

// Function to generate PDF from an image - uses base64 to avoid file:// issues
async function generatePdfFromImage(imageContent) {
  return new Promise((resolve, reject) => {
    try {
      // Convert to base64 to avoid file:// protocol issues
      const imgBase64 = imageContent.toString('base64');
      
      const tempHtmlPath = path.join(tempDir, `worker-img-html-${process.pid}-${Date.now()}.html`);
      const html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
</head>
<body>
  <img src="data:image/png;base64,${imgBase64}" width="1200" alt="Generated Content">
</body>
</html>`;
      
      fs.writeFileSync(tempHtmlPath, html, 'utf8');
      
      const tempPdfPath = path.join(tempDir, `worker-img-pdf-${process.pid}-${Date.now()}.pdf`);
      
      const cmd = `wkhtmltopdf --quiet --enable-local-file-access --encoding UTF-8 --minimum-font-size 12 --dpi 300 --margin-top 0 --margin-bottom 0 --margin-left 0 --margin-right 0 --page-width 210mm --page-height 400mm --zoom 1.5 --load-error-handling ignore "file://${tempHtmlPath}" "${tempPdfPath}"`;
      
      console.log(`Worker ${process.pid}: Executing wkhtmltopdf command for image-to-pdf`);
      
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
              console.error(`Worker ${process.pid}: Error reading generated PDF: ${readError.message}`);
            }
          }
          
          if (error) {
            console.error(`Worker ${process.pid}: Error converting image to PDF: ${error.message}`);
            console.error(`Worker ${process.pid}: stderr: ${stderr}`);
            reject(error);
            return;
          }
          
          reject(new Error("Failed to generate PDF from image"));
        } catch (cleanupError) {
          console.error(`Worker ${process.pid}: Error during cleanup: ${cleanupError.message}`);
          reject(cleanupError);
        }
      });
    } catch (error) {
      console.error(`Worker ${process.pid}: Error in generatePdfFromImage: ${error.message}`);
      reject(error);
    }
  });
}

// Main PDF generation function using HTML-to-image-to-PDF workflow exclusively
async function generatePdf(html, options = {}) {
  try {
    console.log(`Worker ${process.pid}: Starting HTML-to-image-to-PDF generation`);
    const imageResult = await generateImageFromHtml(html, 'png');
    const pdfBuffer = await generatePdfFromImage(imageResult.content);
    return pdfBuffer;
  } catch (error) {
    console.error(`Worker ${process.pid}: HTML-to-image-to-PDF failed: ${error.message}`);
    throw error;
  }
}

// Handle messages from main thread
parentPort.on('message', async (task) => {
  try {
    console.log(`Worker ${process.pid} received task: ${task.id}`);
    
    const pdfBuffer = await generatePdf(task.html, task.options);
    
    // Send result back to main thread
    parentPort.postMessage({ 
      id: task.id, 
      buffer: pdfBuffer 
    });
  } catch (error) {
    // Send error back to main thread
    parentPort.postMessage({ 
      id: task.id, 
      error: error.message 
    });
  }
});

// Signal that the worker is ready
parentPort.postMessage({ status: 'ready', pid: process.pid });