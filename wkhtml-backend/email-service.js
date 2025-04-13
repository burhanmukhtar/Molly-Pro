// Enhanced email processing service with batch operations and concurrency control

const nodemailer = require('nodemailer');
const logger = require('./logger');
const config = require('./config');
const { processDynamicTags, generateCustomTagValues, Semaphore } = require('./utils');
const { createAttachment } = require('./attachment-service');

// Connection pool for SMTP servers
const transporterPool = new Map();

// Get or create a transporter for an SMTP server
async function getTransporter(smtpInfo) {
  // Create a unique key for this SMTP config
  const smtpHost = smtpInfo.host || smtpInfo.server || smtpInfo.smtp_server || '';
  const smtpPort = parseInt(smtpInfo.port || '587');
  const smtpUser = smtpInfo.username || smtpInfo.email || smtpInfo.user || '';
  
  const key = `${smtpHost}:${smtpPort}:${smtpUser}`;
  
  // Check if we already have a transporter for this SMTP server
  if (!transporterPool.has(key)) {
    logger.info(`Creating new transporter for SMTP: ${smtpHost}:${smtpPort}`);
    
    const smtpPass = smtpInfo.password || smtpInfo.pass || '';
    
    // Create transporter
    const transporter = nodemailer.createTransport({
      host: smtpHost,
      port: smtpPort,
      secure: smtpPort === 465,
      auth: {
        user: smtpUser,
        pass: smtpPass
      },
      connectionTimeout: 30000, // 30 seconds
      greetingTimeout: 30000,
      tls: {
        rejectUnauthorized: false
      },
      pool: true,         // Use pooled connections
      maxConnections: 5,  // Maximum number of connections per SMTP server
      maxMessages: 100    // Maximum number of messages per connection
    });
    
    // We don't verify immediately - we'll verify on first use
    transporterPool.set(key, { 
      transporter, 
      verified: false,
      smtpInfo
    });
  }
  
  return transporterPool.get(key);
}

// Verify a transporter if not already verified
async function verifyTransporter(transporterInfo) {
  if (transporterInfo.verified) {
    return true;
  }
  
  try {
    const { transporter, smtpInfo } = transporterInfo;
    
    // Verify connection
    logger.info(`Verifying SMTP connection to ${smtpInfo.host}`);
    await transporter.verify();
    
    // Mark as verified
    transporterInfo.verified = true;
    logger.info(`SMTP connection successful: ${smtpInfo.host}`);
    
    return true;
  } catch (error) {
    logger.error(`SMTP verification failed: ${error.message}`);
    
    const smtpInfo = transporterInfo.smtpInfo;
    const errorDetail = {
      username: smtpInfo.username || '',
      smtp: smtpInfo.host || '',
      error: `Connection error: ${error.message}`,
      timestamp: new Date().toISOString()
    };
    
    config.failedSmtps.push(errorDetail);
    
    return false;
  }
}

async function validateSmtpServer(smtpInfo) {
  try {
    // Extract SMTP details with fallbacks
    const smtpHost = smtpInfo.host || smtpInfo.server || smtpInfo.smtp_server || '';
    const smtpUser = smtpInfo.username || smtpInfo.email || smtpInfo.user || '';
    const smtpPass = smtpInfo.password || smtpInfo.pass || '';
    
    // Debug logging
    logger.info(`SMTP Details - Host: ${smtpHost}, User: ${smtpUser}`);
    
    if (!smtpHost || !smtpUser || !smtpPass) {
      const errorDetail = {
        username: smtpUser,
        smtp: smtpHost,
        error: "Missing required SMTP parameters",
        timestamp: new Date().toISOString()
      };
      config.failedSmtps.push(errorDetail);
      return { 
        isValid: false, 
        error: `Missing required SMTP parameters for ${smtpUser}` 
      };
    }
    
    // Get a transporter
    const transporterInfo = await getTransporter(smtpInfo);
    
    // Verify the transporter
    const isValid = await verifyTransporter(transporterInfo);
    
    return { isValid, error: isValid ? null : "Connection failed" };
    
  } catch (error) {
    logger.error(`SMTP validation failed: ${error.message}`);
    
    const errorDetail = {
      username: smtpInfo.username || '',
      smtp: smtpInfo.host || '',
      error: `Connection error: ${error.message}`,
      timestamp: new Date().toISOString()
    };
    config.failedSmtps.push(errorDetail);
    
    return { 
      isValid: false, 
      error: `SMTP validation failed: ${error.message}` 
    };
  }
}

// Create batches of emails for each SMTP server
function createBatches(smtpServers, emailList) {
  const batches = [];
  const batchSize = Math.ceil(emailList.length / smtpServers.length);
  
  logger.info(`Creating ${smtpServers.length} batches with approximately ${batchSize} emails per batch`);
  
  for (let i = 0; i < smtpServers.length; i++) {
    const startIndex = i * batchSize;
    const endIndex = Math.min(startIndex + batchSize, emailList.length);
    const batchEmails = emailList.slice(startIndex, endIndex);
    
    if (batchEmails.length > 0) {
      batches.push({
        smtp: smtpServers[i],
        emails: batchEmails
      });
      
      logger.info(`Batch ${i+1}: SMTP ${smtpServers[i].host} with ${batchEmails.length} emails`);
    }
  }
  
  return batches;
}

async function sendEmailBatch(batch, emailParams, concurrencyLevel, delay) {
  if (batch.emails.length === 0) return;
  
  try {
    // Get a transporter from the pool
    const transporterInfo = await getTransporter(batch.smtp);
    
    // Verify the transporter
    const isValid = await verifyTransporter(transporterInfo);
    
    if (!isValid) {
      logger.error(`SMTP connection verification failed for ${batch.smtp.host}`);
      
      // Mark all emails in this batch as failed
      batch.emails.forEach(email => {
        config.emailStats.emailsFailed++;
        config.emailStats.failedEmails.push({
          email: email.email || '',
          smtp_username: batch.smtp.username || '',
          smtp_server: batch.smtp.host || '',
          error: `SMTP connection failed`,
          timestamp: new Date().toISOString()
        });
      });
      
      config.updateStats();
      return;
    }
    
    // Get the verified transporter
    const transporter = transporterInfo.transporter;
    
    // Create a semaphore to control concurrency
    const semaphore = new Semaphore(concurrencyLevel);
    
    // Process all emails in the batch with controlled concurrency
    const promises = [];
    
    for (let i = 0; i < batch.emails.length; i++) {
      // Skip if stop sending is requested
      if (config.stopSending) {
        logger.info(`Stopping batch processing at email ${i+1}/${batch.emails.length}`);
        break;
      }
      
      const emailInfo = batch.emails[i];
      
      // Create a promise that will send the email when the semaphore allows
      const emailPromise = (async () => {
        try {
          await semaphore.acquire();
          
          // Apply delay if needed (except for the first email in each parallelized group)
          if (i > 0 && delay > 0) {
            await new Promise(resolve => setTimeout(resolve, delay * 1000));
          }
          
          return await sendEmail(transporter, emailInfo, { ...emailParams, smtpInfo: batch.smtp });
        } finally {
          semaphore.release();
        }
      })();
      
      promises.push(emailPromise);
    }
    
    // Wait for all emails in this batch to complete
    await Promise.all(promises);
    
  } catch (error) {
    logger.error(`Error processing batch: ${error.message}`);
  }
}

async function sendEmail(transporter, emailInfo, emailParams) {
  const maxRetries = 2;
  let retryCount = 0;
  
  while (retryCount <= maxRetries) {
    try {
      // Extract recipient details
      const recipientEmail = emailInfo.email || emailInfo.mail || '';
      const recipientName = emailInfo.name || (recipientEmail ? recipientEmail.split('@')[0] : '');
      
      if (!recipientEmail) {
        logger.error(`Missing recipient email address`);
        config.emailStats.emailsFailed++;
        config.emailStats.failedEmails.push({
          email: '',
          smtp_username: emailParams.smtpInfo.username || '',
          smtp_server: emailParams.smtpInfo.host || '',
          error: "Missing recipient email address",
          timestamp: new Date().toISOString()
        });
        config.updateStats();
        return false;
      }
      
      // Process dynamic tags
      if (!emailInfo.date_value) {
        emailInfo.date_value = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
      }
      
      if (!emailInfo.invoice_value) {
        const now = new Date();
        const year = now.getFullYear().toString().slice(2);
        const month = (now.getMonth() + 1).toString().padStart(2, '0');
        const randomPart = Math.floor(Math.random() * 10000).toString().padStart(4, '0');
        emailInfo.invoice_value = `INV-${year}${month}-${randomPart}`;
      }
      
      // Generate custom tag values for this email
      const customTagValues = generateCustomTagValues(emailParams.customTags);
      
      // Process attachment name
      const processedAttachmentName = emailParams.attachmentName 
        ? processDynamicTags(emailParams.attachmentName, emailInfo, customTagValues) 
        : null;
      
      // Process email content
      const processedSubject = processDynamicTags(emailParams.subject, emailInfo, customTagValues);
      const processedPlain = processDynamicTags(emailParams.plainMessage, emailInfo, customTagValues);
      const processedHtml = processDynamicTags(emailParams.htmlMessage, emailInfo, customTagValues);
      
      // Create email options
      const mailOptions = {
        from: {
          name: emailParams.senderName,
          address: emailParams.smtpInfo.username || emailParams.smtpInfo.email || emailParams.smtpInfo.user
        },
        to: {
          name: recipientName,
          address: recipientEmail
        },
        subject: processedSubject
      };
      
      // Add text part if provided
      if (processedPlain) {
        mailOptions.text = processedPlain;
      }
      
      // Add HTML part if provided
      if (processedHtml) {
        mailOptions.html = processedHtml;
      }
      
      // Add attachment if specified
      if (emailParams.attachmentType !== 'none') {
        const attachment = await createAttachment(
          emailParams.attachmentType,
          emailParams.attachmentHtml,
          emailInfo,
          customTagValues
        );
        
        if (attachment) {
          // Default filenames based on type if none provided
          let finalAttachmentName = processedAttachmentName;
          
          if (!finalAttachmentName) {
            if (emailParams.attachmentType === 'pdf') {
              finalAttachmentName = 'document.pdf';
            } else if (emailParams.attachmentType === 'jpeg') {
              finalAttachmentName = 'image.jpg';
            } else if (emailParams.attachmentType === 'png') {
              finalAttachmentName = 'image.png';
            } else if (emailParams.attachmentType === 'word') {
              finalAttachmentName = 'document.doc';
            }
          }
          
          // Add proper extension if not already present
          if (finalAttachmentName && !finalAttachmentName.toLowerCase().endsWith(attachment.extension)) {
            finalAttachmentName += attachment.extension;
          }
          
          mailOptions.attachments = [{
            filename: finalAttachmentName,
            content: attachment.content,
            contentType: attachment.contentType
          }];
        }
      }
      
      // Send the email
      logger.info(`Sending email to ${recipientEmail}`);
      await transporter.sendMail(mailOptions);
      
      // Update statistics
      config.emailStats.emailsSent++;
      config.updateStats();
      
      logger.info(`Email sent to ${recipientEmail}`);
      return true;
      
    } catch (error) {
      const errorMsg = error.message || 'Unknown error';
      logger.error(`Error sending email to ${emailInfo.email || 'unknown'}: ${errorMsg}`);
      
      // Check if this is a retriable error
      const retriableErrors = [
        'ECONNRESET', 
        'EPIPE',
        'ETIMEDOUT',
        'ECONNREFUSED',
        'connection closed',
        'connection lost',
        'connection error'
      ];
      
      const isRetriable = retriableErrors.some(errType => 
        errorMsg.toLowerCase().includes(errType.toLowerCase())
      );
      
      if (isRetriable && retryCount < maxRetries) {
        retryCount++;
        logger.warn(`Retrying email to ${emailInfo.email || 'unknown'} (attempt ${retryCount} of ${maxRetries})`);
        await new Promise(resolve => setTimeout(resolve, 2000)); // 2 second delay before retry
      } else {
        // Record failure after all retries
        config.emailStats.emailsFailed++;
        config.emailStats.failedEmails.push({
          email: emailInfo.email || '',
          smtp_username: emailParams.smtpInfo.username || '',
          smtp_server: emailParams.smtpInfo.host || '',
          error: errorMsg,
          timestamp: new Date().toISOString()
        });
        config.updateStats();
        return false;
      }
    }
  }
  
  return false;
}

// Process emails using batching with SMTP-level parallelism and email-level concurrency
async function processEmails(
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
  customTags = {},
  dateTags = null
) {
  // Reset stats and flags
  config.emailStats.smtpFailed = 0;
  config.emailStats.smtpSuccess = 0;
  config.emailStats.emailsSent = 0;
  config.emailStats.emailsFailed = 0;
  config.emailStats.failedEmails = [];
  config.emailStats.totalEmails = emailList.length;
  config.emailStats.isRunning = true;
  config.emailStats.validSmtpCount = 0;
  
  // Reset failed SMTPs list
  config.failedSmtps.length = 0;
  
  // Reset stop flag
  config.resetStopFlag();
  
  config.updateStats();
  
  // Debug print the SMTP list
  smtpList.forEach((smtp, i) => {
    logger.info(`SMTP Server #${i+1}: ${JSON.stringify(smtp)}`);
  });
  
  // Validate SMTP servers
  logger.info(`Validating ${smtpList.length} SMTP servers...`);
  const validSmtpServers = [];
  const smtpErrors = [];
  
  for (const smtp of smtpList) {
    // Check for stop request during validation
    if (config.stopSending) {
      logger.info("Stopping email process during SMTP validation (stop requested)");
      config.emailStats.isRunning = false;
      config.updateStats();
      return;
    }
    
    // Basic validation that required fields exist
    const smtpHost = smtp.host || smtp.server || smtp.smtp_server || '';
    const smtpUser = smtp.username || smtp.email || smtp.user || '';
    const smtpPass = smtp.password || smtp.pass || '';
    
    logger.info(`Checking SMTP: Host=${smtpHost}, User=${smtpUser}`);
    
    if (!smtpHost || !smtpUser || !smtpPass) {
      const errorMessage = `Missing required SMTP parameters for ${smtpUser}`;
      logger.error(`SMTP validation failed: ${errorMessage}`);
      config.emailStats.smtpFailed++;
      smtpErrors.push({
        smtp: smtpHost,
        username: smtpUser,
        error: errorMessage
      });
      continue;
    }
    
    // Validate this SMTP server
    const { isValid, error } = await validateSmtpServer(smtp);
    
    if (isValid) {
      validSmtpServers.push(smtp);
      config.emailStats.smtpSuccess++;
      config.emailStats.validSmtpCount++;
      logger.info(`SMTP connection successful: ${smtpHost}`);
    } else {
      config.emailStats.smtpFailed++;
      smtpErrors.push({
        smtp: smtpHost,
        username: smtpUser,
        error: error
      });
      logger.error(`SMTP connection failed: ${smtpHost} - ${error}`);
    }
    
    config.updateStats();
  }
  
  // Emit SMTP validation results after all validations
  config.emitSmtpValidationResults();
  
  // Check if we have any valid SMTP servers
  if (validSmtpServers.length === 0) {
    logger.error("No valid SMTP servers found");
    config.emailStats.isRunning = false;
    config.updateStats();
    return;
  }
  
  // Handle threads parameter - sanitize and make sure it's valid
  let threadCount = parseInt(threads);
  if (isNaN(threadCount) || threadCount < 1) {
    logger.warn(`Invalid thread count: ${threads}, defaulting to 1`);
    threadCount = 1;
  } else if (threadCount > 100) {
    logger.warn(`Thread count ${threadCount} exceeds maximum, limiting to 100`);
    threadCount = 100;
  }
  
  // Calculate concurrency per SMTP server
  const smtpCount = validSmtpServers.length;
  let concurrencyPerSmtp = Math.max(1, Math.floor(threadCount / smtpCount));
  
  logger.info(`Using ${smtpCount} SMTP servers with ${concurrencyPerSmtp} concurrent emails per server`);
  
  // Create batches
  const batches = createBatches(validSmtpServers, emailList);
  
  // Email parameters (common for all emails)
  const emailParams = {
    subject,
    senderName,
    plainMessage,
    htmlMessage,
    attachmentType,
    attachmentHtml,
    attachmentName,
    customTags,
    dateTags
  };
  
  try {
    // Process all batches in parallel (one batch per SMTP server)
    const batchPromises = batches.map(batch => {
      // Process this batch with the calculated concurrency level
      return sendEmailBatch(batch, emailParams, concurrencyPerSmtp, delay);
    });
    
    // Wait for all batches to complete
    await Promise.all(batchPromises);
    
    logger.info(`Email sending completed. Sent: ${config.emailStats.emailsSent}, Failed: ${config.emailStats.emailsFailed}`);
  } catch (error) {
    logger.error(`Error in process_emails: ${error.message}`);
  } finally {
    config.emailStats.isRunning = false;
    config.updateStats();
    
    // Removed reference to cleanupPuppeteer here
    
    if (config.stopSending) {
      logger.info(`Email sending stopped by user request. Sent: ${config.emailStats.emailsSent}, Failed: ${config.emailStats.emailsFailed}`);
    }
  }
}

// Export the processEmails function
module.exports = {
  processEmails
};