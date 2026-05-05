import logging
import sys

# Create a custom logger
logger = logging.getLogger("currency_analyzer")
logger.setLevel(logging.INFO)

# Create handlers (Console and File)
console_handler = logging.StreamHandler(sys.stdout)
file_handler = logging.FileHandler("analyzer.log", encoding="utf-8")

# Create formatters and add them to handlers
log_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(log_format)
file_handler.setFormatter(log_format)

# Add handlers to the logger (prevent duplicate handlers)
if not logger.handlers:
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)