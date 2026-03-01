# Refactored Code for main.py

import logging
import datetime
from safe_log_to_sheets import log_to_sheets

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Utility function to log data safely

def log_data(data):
    try:
        log_to_sheets(data)
        logger.info('Successfully logged data to sheets.')
    except Exception as e:
        logger.error(f'Failed to log data: {e}')

# Extraction functions

def extract_weight(data):
    weight = data.get('weight', 'N/A')
    logger.debug(f'Extracted weight: {weight}')
    return weight


def extract_hrv(data):
    hrv = data.get('hrv', 'N/A')
    logger.debug(f'Extracted HRV: {hrv}')
    return hrv


def extract_sleep_score(data):
    sleep_score = data.get('sleep_score', 'N/A')
    logger.debug(f'Extracted Sleep Score: {sleep_score}')
    return sleep_score

# Activity class to encapsulate activity data

class Activity:
    def __init__(self, weight, hrv, sleep_score, timestamp):
        self.weight = weight
        self.hrv = hrv
        self.sleep_score = sleep_score
        self.timestamp = timestamp

    def __repr__(self):
        return f'Activity(weight={self.weight}, hrv={self.hrv}, sleep_score={self.sleep_score}, timestamp={self.timestamp})'

# Function to process activities

def process_activities(activity_data):
    activities = []

    for data in activity_data:
        weight = extract_weight(data)
        hrv = extract_hrv(data)
        sleep_score = extract_sleep_score(data)
        timestamp = datetime.datetime.utcnow().isoformat()
        activity = Activity(weight, hrv, sleep_score, timestamp)
        activities.append(activity)

    logger.debug(f'Unsorted activities: {activities}')
    activities_sorted = sorted(activities, key=lambda x: x.timestamp)
    logger.debug(f'Sorted activities: {activities_sorted}')

    return activities_sorted

if __name__ == '__main__':
    # Example activity data
    example_activity_data = [
        {'weight': 75, 'hrv': 60, 'sleep_score': 85},
        {'weight': 80, 'hrv': 55, 'sleep_score': 90},
    ]

    sorted_activities = process_activities(example_activity_data)
    log_data(sorted_activities)