// Utility functions for the mailer service

const fs = require('fs');
const { parse } = require('csv-parse/sync');
const logger = require('./logger');

// Pre-compile regular expressions for tag replacement
const tagRegexCache = new Map();

// Get compiled regex for a tag
function getTagRegex(tag) {
  if (!tagRegexCache.has(tag)) {
    tagRegexCache.set(tag, new RegExp(`\\${tag}`, 'g'));
  }
  return tagRegexCache.get(tag);
}

// Semaphore implementation for concurrency control
class Semaphore {
  constructor(max) {
    this.max = max;
    this.count = 0;
    this.waiting = [];
  }

  async acquire() {
    if (this.count < this.max) {
      this.count++;
      return Promise.resolve();
    }

    // Wait until a resource becomes available
    return new Promise(resolve => {
      this.waiting.push(resolve);
    });
  }

  release() {
    this.count--;
    
    // Process all waiting tasks that can be executed
    while (this.waiting.length > 0 && this.count < this.max) {
      this.count++;
      const next = this.waiting.shift();
      next();
    }
  }
}

// Utility function to generate random strings
function generateRandomString(length = 10, charType = 'alphanumeric', caseType = 'mixed') {
  let chars = '';
  
  if (charType === 'alphanumeric') {
    chars += (caseType === 'lower' || caseType === 'mixed') ? 'abcdefghijklmnopqrstuvwxyz' : '';
    chars += (caseType === 'upper' || caseType === 'mixed') ? 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' : '';
    chars += '0123456789';
  } else if (charType === 'alpha') {
    chars += (caseType === 'lower' || caseType === 'mixed') ? 'abcdefghijklmnopqrstuvwxyz' : '';
    chars += (caseType === 'upper' || caseType === 'mixed') ? 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' : '';
  } else if (charType === 'numeric') {
    chars = '0123456789';
  } else if (charType === 'hex') {
    chars = '0123456789abcdefABCDEF';
  }
  
  let result = '';
  for (let i = 0; i < length; i++) {
    result += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  
  return result;
}

// Generate an invoice number
function generateInvoiceNumber() {
  const now = new Date();
  const year = now.getFullYear().toString().slice(2);
  const month = (now.getMonth() + 1).toString().padStart(2, '0');
  const randomPart = Math.floor(Math.random() * 10000).toString().padStart(4, '0');
  
  return `INV-${year}${month}-${randomPart}`;
}

// Get formatted date string
function getFormattedDate() {
  const now = new Date();
  const options = { year: 'numeric', month: 'long', day: 'numeric' };
  return now.toLocaleDateString('en-US', options);
}

// Process dynamic tags in content
function processDynamicTags(content, emailInfo, customTagValues = null) {
  if (!content) return content;
  
  // Create a copy to avoid modifying the original
  let result = String(content);
  
  // Process standard tags using cached regexes
  result = result.replace(getTagRegex('$email'), emailInfo.email || '');
  result = result.replace(getTagRegex('$name'), emailInfo.name || '');
  
  // Generate random ID if needed
  if (result.includes('$ranId') && !emailInfo.random_id) {
    emailInfo.random_id = generateRandomString(10, 'alphanumeric', 'upper');
  }
  
  if (emailInfo.random_id) {
    result = result.replace(getTagRegex('$ranId'), emailInfo.random_id);
  }
  
  // Process date tag
  if (result.includes('$date') && !emailInfo.date_value) {
    emailInfo.date_value = getFormattedDate();
  }
  
  if (emailInfo.date_value) {
    result = result.replace(getTagRegex('$date'), emailInfo.date_value);
  }
  
  // Process invoice tag
  if (result.includes('$invoice') && !emailInfo.invoice_value) {
    emailInfo.invoice_value = generateInvoiceNumber();
  }
  
  if (emailInfo.invoice_value) {
    result = result.replace(getTagRegex('$invoice'), emailInfo.invoice_value);
  }
  
  // Process custom tags
  if (customTagValues) {
    for (const [tagName, tagValue] of Object.entries(customTagValues)) {
      const tagPlaceholder = `$${tagName}`;
      if (result.includes(tagPlaceholder)) {
        result = result.replace(getTagRegex(tagPlaceholder), String(tagValue));
      }
    }
  }
  
  return result;
}

// Generate values for all custom tags
function generateCustomTagValues(customTags = {}) {
  const tagValues = {};
  
  // Add date and invoice tags
  tagValues.date = getFormattedDate();
  tagValues.invoice = generateInvoiceNumber();
  
  // Add custom tags defined by the user
  for (const [tagName, config] of Object.entries(customTags)) {
    tagValues[tagName] = generateRandomString(
      config.length || 10,
      config.type || 'alphanumeric',
      config.case || 'mixed'
    );
  }
  
  return tagValues;
}

// Read and parse CSV file with optimized approach
async function readCsvFile(filePath) {
  try {
    // First try with UTF-8 encoding and comma delimiter (most common)
    try {
      const fileContent = fs.readFileSync(filePath, { encoding: 'utf-8' });
      const records = parse(fileContent, {
        columns: true,
        delimiter: ',', 
        skip_empty_lines: true,
        trim: true
      });
      
      if (records && records.length > 0) {
        logger.info(`Successfully parsed ${records.length} rows with default settings`);
        return normalizeRecords(records);
      }
    } catch (firstError) {
      logger.warn(`Failed with default parser settings: ${firstError.message}`);
      // Continue to fallback methods
    }
    
    // Fallback: Try different encodings and delimiters
    const encodings = ['latin1', 'utf-8'];
    const delimiters = [',', ';', '\t', '|'];
    
    for (const encoding of encodings) {
      let content = fs.readFileSync(filePath, { encoding });
      // Remove BOM if present
      if (content.charCodeAt(0) === 0xFEFF) {
        content = content.slice(1);
      }
      
      for (const delimiter of delimiters) {
        try {
          const records = parse(content, {
            columns: true,
            delimiter,
            skip_empty_lines: true,
            trim: true
          });
          
          if (records && records.length > 0) {
            logger.info(`Successfully parsed ${records.length} rows with encoding=${encoding}, delimiter='${delimiter}'`);
            return normalizeRecords(records);
          }
        } catch (error) {
          logger.debug(`Failed parsing with encoding=${encoding}, delimiter='${delimiter}': ${error.message}`);
        }
      }
    }
    
    logger.error("All CSV parsing attempts failed");
    return [];
    
  } catch (error) {
    logger.error(`Error reading CSV file: ${error.message}`);
    return [];
  }
}

// Normalize CSV records
function normalizeRecords(records) {
  // Detect if this is likely an SMTP file or an email list
  const firstRecord = records[0] || {};
  const keys = Object.keys(firstRecord).map(k => k.toLowerCase());
  
  const hasSmtpKeys = keys.some(key => 
    ['host', 'server', 'smtp_server', 'smtp', 'hostname'].includes(key)
  );
  
  const hasEmailKeys = keys.some(key => 
    ['email', 'mail', 'e-mail', 'emailaddress'].includes(key)
  );
  
  return records.map(row => {
    const normalized = {};
    
    // Normalize all keys
    for (const [key, value] of Object.entries(row)) {
      if (key === null) continue;
      const normalizedKey = key.trim().toLowerCase().replace(/\s+/g, '_');
      normalized[normalizedKey] = value;
    }
    
    // Add mapped fields for SMTP servers
    if (hasSmtpKeys) {
      // Map server fields
      if (!normalized.host || normalized.host === '') {
        for (const key of ['server', 'smtp_server', 'smtp', 'hostname']) {
          if (normalized[key] && normalized[key] !== '') {
            normalized.host = normalized[key];
            break;
          }
        }
      }
      
      // Map username fields
      if (!normalized.username || normalized.username === '') {
        for (const key of ['email', 'user', 'login', 'account']) {
          if (normalized[key] && normalized[key] !== '') {
            normalized.username = normalized[key];
            break;
          }
        }
      }
      
      // Map password fields
      if (!normalized.password || normalized.password === '') {
        for (const key of ['pass', 'pwd', 'secret']) {
          if (normalized[key] && normalized[key] !== '') {
            normalized.password = normalized[key];
            break;
          }
        }
      }
    }
    
    // Add mapped fields for email recipients
    if (hasEmailKeys) {
      // Map email fields
      if (!normalized.email || normalized.email === '') {
        for (const key of ['mail', 'e-mail', 'emailaddress', 'address']) {
          if (normalized[key] && normalized[key] !== '') {
            normalized.email = normalized[key];
            break;
          }
        }
      }
      
      // Map name fields
      if (!normalized.name || normalized.name === '') {
        for (const key of ['fullname', 'full_name', 'firstname', 'first_name', 'customer', 'user']) {
          if (normalized[key] && normalized[key] !== '') {
            normalized.name = normalized[key];
            break;
          }
        }
      }
    }
    
    return normalized;
  });
}

// Export utility functions and classes
module.exports = {
  Semaphore,
  generateRandomString,
  generateInvoiceNumber,
  generateRandomString, 
  getFormattedDate,
  processDynamicTags,
  generateCustomTagValues,
  readCsvFile
};