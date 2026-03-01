import logging
import traceback
from datetime import datetime

# Setting up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

class SafeLogger:
    def safe_log_to_sheets(self, data):
        try:
            # Log data securely to Google Sheets or any other endpoint
            logging.info('Logging to sheets: %s', data)
            # Implement the actual logging mechanism here
        except Exception as e:
            logging.error('Failed to log to sheets: %s', str(e))
            logging.debug(traceback.format_exc())

class Activity:
    def __init__(self, name, weight, hrv, sleep_score, timestamp):
        self.name = name
        self.weight = weight
        self.hrv = hrv
        self.sleep_score = sleep_score
        self.timestamp = timestamp

    def __repr__(self):
        return f'Activity({self.name}, {self.weight}, {self.hrv}, {self.sleep_score}, {self.timestamp})'

def extract_data():
    # This function should be replaced with actual data extraction logic
    activities = [
        Activity('Run', 70, 50, 80, '2026-02-28 07:30:00'),
        Activity('Sleep', 70, 45, 90, '2026-03-01 00:00:00'),
        Activity('Cycle', 70, 55, 75, '2026-02-28 09:00:00'),
    ]
    return activities

if __name__ == '__main__':
    logger = SafeLogger()
    try:
        activities = extract_data()
        activities.sort(key=lambda x: datetime.strptime(x.timestamp, '%Y-%m-%d %H:%M:%S'))
        for activity in activities:
            logger.safe_log_to_sheets(activity)
            logging.info('Processed activity: %s', activity)
    except Exception as e:
        logging.error('An error occurred: %s', str(e))
        logging.debug(traceback.format_exc())