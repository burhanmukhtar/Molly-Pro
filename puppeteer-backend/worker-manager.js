// Worker Manager for PDF generation using wkhtmltopdf
// Manages a pool of worker threads for PDF generation

const { Worker } = require('worker_threads');
const path = require('path');
const os = require('os');
const logger = require('./logger');
const { exec } = require('child_process');

// Check if wkhtmltopdf is installed before starting workers
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
  });
}

// Configuration
const MAX_WORKERS = Math.max(2, Math.min(os.cpus().length - 1, 8)); // Use at most 8 workers or CPU count - 1
const WORKER_TIMEOUT = 60000; // 60 seconds timeout for worker tasks

// Worker pool
const workers = [];
const taskQueue = [];
const pendingTasks = new Map();
let nextTaskId = 1;

// Initialize the worker pool
async function initializeWorkerPool() {
  // First check if wkhtmltopdf is installed
  const isInstalled = await checkWkhtmlInstallation();
  if (!isInstalled) {
    logger.error("wkhtmltopdf is not properly installed. Worker pool initialization aborted.");
    return false;
  }
  
  logger.info(`Initializing PDF worker pool with ${MAX_WORKERS} workers`);
  
  for (let i = 0; i < MAX_WORKERS; i++) {
    createWorker();
  }
  
  return true;
}

// Create a new worker
function createWorker() {
  try {
    const worker = new Worker(path.join(__dirname, 'pdfworker.js'));
    
    worker.on('message', message => {
      if (message.status === 'ready') {
        logger.info(`PDF worker ${message.pid} is ready`);
        processNextTask(worker);
      } else if (message.id) {
        const taskId = message.id;
        if (pendingTasks.has(taskId)) {
          const { resolve, reject, timer } = pendingTasks.get(taskId);
          
          // Clear timeout
          clearTimeout(timer);
          
          // Handle result or error
          if (message.error) {
            logger.error(`Worker task ${taskId} failed: ${message.error}`);
            reject(new Error(message.error));
          } else {
            logger.info(`Worker task ${taskId} completed successfully`);
            resolve(message.buffer);
          }
          
          // Remove from pending tasks
          pendingTasks.delete(taskId);
          
          // Process next task
          processNextTask(worker);
        }
      }
    });
    
    worker.on('error', err => {
      logger.error(`Worker error: ${err.message}`);
      
      // Replace the crashed worker
      const index = workers.indexOf(worker);
      if (index !== -1) {
        workers.splice(index, 1);
        createWorker();
      }
    });
    
    worker.on('exit', code => {
      logger.info(`Worker exited with code ${code}`);
      
      // Replace the worker if it wasn't intentionally terminated
      const index = workers.indexOf(worker);
      if (index !== -1) {
        workers.splice(index, 1);
        if (code !== 0) {
          createWorker();
        }
      }
    });
    
    workers.push(worker);
    
  } catch (error) {
    logger.error(`Error creating worker: ${error.message}`);
  }
}

// Process the next task in the queue
function processNextTask(worker) {
  if (taskQueue.length > 0) {
    const task = taskQueue.shift();
    const { id, html, options, resolve, reject } = task;
    
    logger.info(`Assigning task ${id} to worker`);
    
    // Set timeout for the task
    const timer = setTimeout(() => {
      logger.warn(`Task ${id} timed out after ${WORKER_TIMEOUT}ms`);
      
      if (pendingTasks.has(id)) {
        const { reject } = pendingTasks.get(id);
        reject(new Error('PDF generation timed out'));
        pendingTasks.delete(id);
      }
      
      // Kill and replace the worker that might be stuck
      try {
        const index = workers.indexOf(worker);
        if (index !== -1) {
          workers.splice(index, 1);
          worker.terminate();
          createWorker();
        }
      } catch (e) {
        logger.error(`Error terminating worker: ${e.message}`);
      }
    }, WORKER_TIMEOUT);
    
    // Store task information
    pendingTasks.set(id, { resolve, reject, timer, worker });
    
    // Send task to worker
    worker.postMessage({ id, html, options });
  }
}

// Generate PDF using worker
function generatePdf(html, options = {}) {
  return new Promise((resolve, reject) => {
    if (workers.length === 0) {
      logger.error("No workers available. PDF generation failed.");
      reject(new Error("PDF worker pool not initialized"));
      return;
    }
    
    const taskId = nextTaskId++;
    logger.info(`Creating PDF generation task ${taskId}`);
    
    // Add task to queue
    taskQueue.push({
      id: taskId,
      html,
      options,
      resolve,
      reject
    });
    
    // Find an available worker
    for (const worker of workers) {
      const isWorkerBusy = Array.from(pendingTasks.values()).some(task => task.worker === worker);
      if (!isWorkerBusy) {
        processNextTask(worker);
        return;
      }
    }
    
    // If we get here, all workers are busy, the task will be processed when a worker becomes available
    logger.info(`All workers busy, task ${taskId} queued for later processing`);
  });
}

// Shutdown the worker pool
async function shutdown() {
  logger.info("Shutting down PDF worker pool");
  
  // Cancel all pending tasks
  for (const [id, { reject, timer }] of pendingTasks.entries()) {
    clearTimeout(timer);
    reject(new Error('Worker pool shutdown'));
    pendingTasks.delete(id);
  }
  
  // Terminate all workers
  return Promise.all(workers.map(worker => {
    return new Promise((resolve) => {
      try {
        worker.terminate().then(resolve).catch(resolve);
      } catch (error) {
        logger.error(`Error terminating worker: ${error.message}`);
        resolve();
      }
    });
  }));
}

// Export functions
module.exports = {
  initializeWorkerPool,
  generatePdf,
  shutdown
};