#!/usr/bin/env node
// Enhanced mailer service with batch SMTP processing and concurrency control

const express = require('express');
const cors = require('cors');
const bodyParser = require('body-parser');
const multer = require('multer');
const http = require('http');
const socketIO = require('socket.io');
const fs = require('fs');
const path = require('path');
const os = require('os');

// Import modules
const logger = require('./logger');
const config = require('./config');
const { readCsvFile } = require('./utils');
const { processEmails } = require('./email-service');

// Initialize worker pool for PDF generation
const workerManager = require('./worker-manager');
workerManager.initializeWorkerPool().then(initialized => {
  if (initialized) {
    logger.info("PDF worker pool initialized successfully");
  } else {
    logger.error("Failed to initialize PDF worker pool - check if wkhtmltopdf is installed");
  }
});

// Temporary directory for file operations
const tempDir = os.tmpdir();

// Create Express app
const app = express();
const server = http.createServer(app);
const io = socketIO(server, {
  cors: {
    origin: '*',
    methods: ['GET', 'POST']
  },
  pingTimeout: 60000,
  pingInterval: 25000
});

// Set the io instance in config
config.setIo(io);
// Add this after initializing your Express app
app.use(express.static(__dirname));

// Configure middleware
app.use(cors());
app.use(bodyParser.json({ limit: '50mb' }));
app.use(bodyParser.urlencoded({ extended: true, limit: '50mb' }));

// Configure file upload
const upload = multer({ dest: tempDir });

// Socket.IO event handlers
io.on('connection', (socket) => {
  logger.info('Client connected');
  config.setConnected(true);
  
  // Send current status to the newly connected client
  socket.emit('status', config.emailStats);
  
  // Handle client disconnect
  socket.on('disconnect', () => {
    logger.info('Client disconnected');
    config.setConnected(false);
  });
});

// Serve the dashboard HTML file
app.get('/', (req, res) => {
  try {
    res.sendFile(path.join(__dirname, 'scary.html'));
  } catch (error) {
    logger.error(`Error serving dashboard: ${error.message}`);
    res.status(500).send('Error loading dashboard');
  }
});

// Connection check endpoint
app.get('/connect', (req, res) => {
  try {
    res.json({ 
      status: 'connected',
      message: 'Mailer service is ready to use', 
      timestamp: new Date().toISOString()
    });
  } catch (error) {
    logger.error(`Error in /connect: ${error.message}`);
    res.status(500).json({ error: error.message });
  }
});

// Main endpoint to handle email sending
app.post('/send-mails', upload.fields([
  { name: 'smtpFile', maxCount: 1 },
  { name: 'emailFile', maxCount: 1 }
]), async (req, res) => {
  try {
    // Check if required files are uploaded
    if (!req.files || !req.files.smtpFile || !req.files.emailFile) {
      return res.status(400).json({ error: 'SMTP and Email CSV files are required' });
    }
    
    // Parse CSV files
    const smtpList = await readCsvFile(req.files.smtpFile[0].path);
    const emailList = await readCsvFile(req.files.emailFile[0].path);
    
    // Check if parsing was successful
    if (smtpList.length === 0) {
      return res.status(400).json({ error: 'Failed to parse SMTP list or file is empty' });
    }
    
    if (emailList.length === 0) {
      return res.status(400).json({ error: 'Failed to parse email list or file is empty' });
    }
    
    // Get form parameters with fallback values
    const subject = req.body.subject || 'No Subject';
    const senderName = req.body.senderName || 'Sender';
    const plainMessage = req.body.plainMessage || '';
    const htmlMessage = req.body.htmlMessage || '';
    const attachmentType = req.body.attachmentType || 'none';
    const attachmentHtml = req.body.attachmentHtml || '';
    const attachmentName = req.body.attachmentName || '';
    
    // Parse delay and threads with input validation
    let delay = parseFloat(req.body.delay || '0');
    if (isNaN(delay) || delay < 0) delay = 0;
    if (delay > 60) delay = 60; // Cap at 60 seconds
    
    let threads = parseInt(req.body.threads || '1');
    if (isNaN(threads) || threads < 1) threads = 1;
    if (threads > 100) threads = 100; // Cap at 100 threads
    
    // Process custom tags if provided
    if (req.body.customTags) {
      try {
        config.customTags = JSON.parse(req.body.customTags);
      } catch (e) {
        logger.error(`Error parsing custom tags: ${e.message}`);
      }
    }
    
    // Process date tags if provided
    let dateTags = null;
    if (req.body.dateTags) {
      try {
        dateTags = JSON.parse(req.body.dateTags);
      } catch (e) {
        logger.error(`Error parsing date tags: ${e.message}`);
      }
    }
    
    // Log the request details
    logger.info(`Email sending request received: ${emailList.length} recipients, ${smtpList.length} SMTP servers, ${threads} threads, ${delay}s delay`);
    
    // Send immediate response to client
    res.json({ 
      success: true,
      message: 'Email sending process started',
      stats: {
        smtpCount: smtpList.length,
        emailCount: emailList.length,
        threads: threads,
        delay: delay
      }
    });
    
    // Start email processing in the background
    processEmails(
      smtpList,
      emailList,
      subject,
      senderName,
      plainMessage,
      htmlMessage,
      attachmentType,
      attachmentHtml,
      attachmentName,
      delay,
      threads,
      config.customTags,
      dateTags
    ).catch(error => {
      logger.error(`Error in processEmails: ${error.message}`);
      config.emailStats.isRunning = false;
      config.updateStats();
    });
    
    // Clean up temporary files
    try {
      fs.unlinkSync(req.files.smtpFile[0].path, () => {});
      fs.unlinkSync(req.files.emailFile[0].path, () => {});
    } catch (e) {
      logger.error(`Error cleaning up temp files: ${e.message}`);
    }
    
  } catch (error) {
    logger.error(`Error in /send-mails: ${error.message}`);
    res.status(500).json({ error: error.message });
  }
});

// Endpoint to stop the email sending process
app.post('/stop-sending', (req, res) => {
  try {
    if (!config.emailStats.isRunning) {
      return res.status(400).json({ error: 'No email sending process is currently running' });
    }
    
    config.stopEmailProcessing();
    logger.info('Stop requested for email sending process');
    
    // Send response
    res.json({ 
      success: true, 
      message: 'Stop signal sent to email process' 
    });
  } catch (error) {
    logger.error(`Error in /stop-sending: ${error.message}`);
    res.status(500).json({ error: error.message });
  }
});

// Endpoint to download failed emails as CSV
app.get('/download-failed-emails', (req, res) => {
  try {
    // Add debug logging
    logger.info(`Download failed emails requested. Count: ${config.emailStats.failedEmails.length}`);
    
    if (!config.emailStats.failedEmails || config.emailStats.failedEmails.length === 0) {
      logger.warn('No failed emails found for download');
      return res.status(404).json({ error: 'No failed emails found' });
    }
    
    // Create CSV content
    const fields = ['email', 'smtp_username', 'smtp_server', 'error', 'timestamp'];
    let csvContent = fields.join(',') + '\n';
    
    // Add each failed email as a CSV row
    config.emailStats.failedEmails.forEach(failure => {
      // Properly escape and quote CSV values
      const csvRow = fields.map(field => {
        const value = failure[field] || '';
        // Escape quotes and wrap in quotes
        return `"${String(value).replace(/"/g, '""')}"`;
      });
      csvContent += csvRow.join(',') + '\n';
    });
    
    // Create a temporary file
    const tempFilePath = path.join(os.tmpdir(), `failed-emails-${Date.now()}.csv`);
    fs.writeFileSync(tempFilePath, csvContent, 'utf8');
    
    // Send the file for download
    res.download(tempFilePath, 'failed_emails.csv', (err) => {
      // Delete temp file after download completes or fails
      fs.unlink(tempFilePath, () => {});
      
      if (err) {
        logger.error(`Error sending download: ${err.message}`);
      } else {
        logger.info('Failed emails CSV sent successfully');
      }
    });
    
  } catch (error) {
    logger.error(`Error in /download-failed-emails: ${error.message}`);
    res.status(500).json({ error: error.message });
  }
});

// Endpoint to download failed SMTP servers as CSV
app.get('/download-failed-smtps', (req, res) => {
  try {
    // Add debug logging
    logger.info(`Download failed SMTPs requested. Count: ${config.failedSmtps.length}`);
    
    if (!config.failedSmtps || config.failedSmtps.length === 0) {
      logger.warn('No failed SMTP servers found for download');
      return res.status(404).json({ error: 'No failed SMTP servers found' });
    }
    
    // Create CSV content
    const fields = ['smtp', 'username', 'error', 'timestamp'];
    let csvContent = fields.join(',') + '\n';
    
    // Add each failed SMTP as a CSV row
    config.failedSmtps.forEach(failure => {
      // Properly escape and quote CSV values
      const csvRow = fields.map(field => {
        const value = failure[field] || '';
        // Escape quotes and wrap in quotes
        return `"${String(value).replace(/"/g, '""')}"`;
      });
      csvContent += csvRow.join(',') + '\n';
    });
    
    // Create a temporary file
    const tempFilePath = path.join(os.tmpdir(), `failed-smtps-${Date.now()}.csv`);
    fs.writeFileSync(tempFilePath, csvContent, 'utf8');
    
    // Send the file for download
    res.download(tempFilePath, 'failed_smtps.csv', (err) => {
      // Delete temp file after download completes or fails
      fs.unlink(tempFilePath, () => {});
      
      if (err) {
        logger.error(`Error sending download: ${err.message}`);
      } else {
        logger.info('Failed SMTPs CSV sent successfully');
      }
    });
    
  } catch (error) {
    logger.error(`Error in /download-failed-smtps: ${error.message}`);
    res.status(500).json({ error: error.message });
  }
});

// Server status endpoint
app.get('/status', (req, res) => {
  try {
    // Add the failed SMTPs to the stats
    const statsWithFailedSmtps = {
      ...config.emailStats,
      failedSmtps: config.failedSmtps
    };
    
    res.json(statsWithFailedSmtps);
  } catch (error) {
    logger.error(`Error in /status: ${error.message}`);
    res.status(500).json({ error: error.message });
  }
});

// Debug endpoints to check current failed emails and SMTPs
app.get('/api/failed-emails', (req, res) => {
  try {
    res.json({
      count: config.emailStats.failedEmails.length,
      emails: config.emailStats.failedEmails
    });
  } catch (error) {
    logger.error(`Error in /api/failed-emails: ${error.message}`);
    res.status(500).json({ error: error.message });
  }
});

app.get('/api/failed-smtps', (req, res) => {
  try {
    res.json({
      count: config.failedSmtps.length,
      smtps: config.failedSmtps
    });
  } catch (error) {
    logger.error(`Error in /api/failed-smtps: ${error.message}`);
    res.status(500).json({ error: error.message });
  }
});

// Custom tags management endpoint
app.post('/update-tags', (req, res) => {
  try {
    const { tags } = req.body;
    
    if (!tags || typeof tags !== 'object') {
      return res.status(400).json({ error: 'Invalid tags format' });
    }
    
    config.customTags = tags;
    logger.info(`Updated custom tags: ${JSON.stringify(config.customTags)}`);
    
    res.json({ success: true, message: 'Custom tags updated' });
  } catch (error) {
    logger.error(`Error in /update-tags: ${error.message}`);
    res.status(500).json({ error: error.message });
  }
});

// API endpoint to upload SMTP list
app.post('/api/upload-smtp', upload.single('file'), async (req, res) => {
  try {
    if (!req.file) {
      return res.status(400).json({ error: 'No file uploaded' });
    }
    
    logger.info(`SMTP file uploaded: ${req.file.originalname}`);
    const smtpList = await readCsvFile(req.file.path);
    
    // Clean up temp file
    fs.unlink(req.file.path, (err) => {
      if (err) logger.error(`Error deleting temp file: ${err.message}`);
    });
    
    if (smtpList.length === 0) {
      return res.status(400).json({ error: 'Could not parse SMTP list or file is empty' });
    }
    
    res.json({ success: true, count: smtpList.length, sample: smtpList.slice(0, 3) });
  } catch (error) {
    logger.error(`Error in /api/upload-smtp: ${error.message}`);
    res.status(500).json({ error: error.message });
  }
});

// API endpoint to upload email list
app.post('/api/upload-emails', upload.single('file'), async (req, res) => {
  try {
    if (!req.file) {
      return res.status(400).json({ error: 'No file uploaded' });
    }
    
    logger.info(`Email list file uploaded: ${req.file.originalname}`);
    const emailList = await readCsvFile(req.file.path);
    
    // Clean up temp file
    fs.unlink(req.file.path, (err) => {
      if (err) logger.error(`Error deleting temp file: ${err.message}`);
    });
    
    if (emailList.length === 0) {
      return res.status(400).json({ error: 'Could not parse email list or file is empty' });
    }
    
    res.json({ success: true, count: emailList.length, sample: emailList.slice(0, 3) });
  } catch (error) {
    logger.error(`Error in /api/upload-emails: ${error.message}`);
    res.status(500).json({ error: error.message });
  }
});

// Handle 404 errors for any other routes
app.use((req, res) => {
  res.status(404).json({ error: 'Endpoint not found' });
});

// Global error handler
app.use((err, req, res, next) => {
  logger.error(`Unhandled error: ${err.message}`);
  res.status(500).json({ error: 'Server error', message: err.message });
});
// Add this after your cors and bodyParser middleware
app.use((req, res, next) => {
  if (req.url.endsWith('.js')) {
    res.set('Content-Type', 'application/javascript');
  }
  next();
});

// Start the server
const PORT = process.env.PORT || 5000;

server.listen(PORT, () => {
  try {
    // Get the instance IP for logging
    const hostname = os.hostname();
    let privateIp = 'unknown';
    
    const networkInterfaces = os.networkInterfaces();
    Object.keys(networkInterfaces).forEach(ifname => {
      networkInterfaces[ifname].forEach(iface => {
        if (iface.family === 'IPv4' && !iface.internal) {
          privateIp = iface.address;
        }
      });
    });
    
    logger.info(`Starting enhanced mailer service on ${hostname} (${privateIp})`);
    logger.info(`Server running on port ${PORT}`);
  } catch (error) {
    logger.error(`Error getting server info: ${error.message}`);
  }
});

// Graceful shutdown handling
process.on('SIGTERM', async () => {
  logger.info('SIGTERM received, shutting down gracefully');
  
  // Shutdown worker pool
  await workerManager.shutdown();
  
  server.close(() => {
    logger.info('Server closed');
    process.exit(0);
  });
});

process.on('SIGINT', async () => {
  logger.info('SIGINT received, shutting down gracefully');
  
  // Shutdown worker pool
  await workerManager.shutdown();
  
  server.close(() => {
    logger.info('Server closed');
    process.exit(0);
  });
});