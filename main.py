import time
import requests

class GeminiAPIError(Exception):
    pass


def call_gemini_api(endpoint, retries=5, backoff_in_seconds=1):
    for i in range(retries):
        try:
            response = requests.get(endpoint)
            response.raise_for_status()  # raise an error for bad responses
            return response.json()
        except requests.exceptions.RequestException as e:
            if i < retries - 1:
                time.sleep(backoff_in_seconds)
                backoff_in_seconds *= 2  # Exponential backoff
            else:
                raise GeminiAPIError(f"API call failed after {retries} attempts: {e}")

# Example usage of the call_gemini_api function
if __name__ == '__main__':
    endpoint = 'https://api.gemini.com/v1/example'  # Replace with actual endpoint
    try:
        data = call_gemini_api(endpoint)
        print(data)
    except GeminiAPIError as e:
        print(e)  # Handle the error appropriately
