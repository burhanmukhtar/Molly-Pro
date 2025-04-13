// Shared state and configuration for the mailer service
const logger = require('./logger');

// Global variables to track email sending progress
const emailStats = {
  smtpFailed: 0,
  smtpSuccess: 0,
  emailsSent: 0,
  emailsFailed: 0,
  failedEmails: [],
  totalEmails: 0,
  isRunning: false,
  validSmtpCount: 0
};

// Failed SMTP servers
const failedSmtps = [];

// Flag to control stopping of email sending
let stopSending = false;

// Socket.io instance (will be set by server.js)
let io = null;

// Connection status
let connected = false;

// Store custom tags configuration
let customTags = {};

// Set io instance
function setIo(ioInstance) {
  io = ioInstance;
}

// Update stats via Socket.IO
function updateStats() {
  if (connected && io) {
    try {
      io.emit('status', emailStats);
      logger.debug(`Emitted stats update: ${JSON.stringify(emailStats)}`);
    } catch (error) {
      logger.error(`Error updating stats via socket: ${error.message}`);
    }
  }
}

// Signal that email sending should stop
function stopEmailProcessing() {
  stopSending = true;
}

// Reset stop flag
function resetStopFlag() {
  stopSending = false;
}

// Set connection status
function setConnected(status) {
  connected = status;
}
// Add function to emit SMTP validation results
function emitSmtpValidationResults() {
  if (connected && io) {
    try {
      io.emit('smtp_validation', {
        valid_count: emailStats.validSmtpCount,
        invalid_count: failedSmtps.length,
        errors: failedSmtps
      });
      logger.debug(`Emitted SMTP validation results: Valid=${emailStats.validSmtpCount}, Invalid=${failedSmtps.length}`);
    } catch (error) {
      logger.error(`Error emitting SMTP validation: ${error.message}`);
    }
  }
}

module.exports = {
  emailStats,
  failedSmtps,
  get stopSending() { return stopSending; },
  stopSending,
  setIo,
  updateStats,
  setConnected,
  customTags,
  stopEmailProcessing,
  resetStopFlag,
  emitSmtpValidationResults
};