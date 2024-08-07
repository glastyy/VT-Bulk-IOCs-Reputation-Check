import pandas as pd
import requests
from tqdm import tqdm
from datetime import datetime
import itertools
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import base64

# Constants
API_KEYS = ['API_KEY_1','API_KEY_2']  # Add more API keys as needed
VT_URLS = {
    'file': 'https://www.virustotal.com/api/v3/files/',
    'domain': 'https://www.virustotal.com/api/v3/domains/',
    'ip': 'https://www.virustotal.com/api/v3/ip_addresses/',
    'url': 'https://www.virustotal.com/api/v3/urls/'
}
MAX_REQUESTS_PER_MINUTE = 1000  # Set this based on your API key limit

# Thread-safe iterator for API keys
api_key_lock = threading.Lock()
api_key_cycle = itertools.cycle(API_KEYS)


def get_vt_report(ioc, ioc_type):
    with api_key_lock:
        api_key = next(api_key_cycle)

    headers = {
        "x-apikey": api_key
    }
    if ioc_type == 'url':
        encoded_ioc = base64.urlsafe_b64encode(ioc.encode()).decode().rstrip("=")
        url = VT_URLS[ioc_type] + encoded_ioc
    else:
        url = VT_URLS[ioc_type] + ioc

    response = requests.get(url, headers=headers)
    print(f"Requesting {url} with API key {api_key[:5]}...")  # Debug statement
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error {response.status_code}: {response.text}")  # Debug statement
        return None


def determine_ioc_type(ioc):
    if ioc.count('.') == 3 and all(part.isdigit() and 0 <= int(part) < 256 for part in ioc.split('.')):
        return 'ip'
    elif '.' in ioc and '/' not in ioc:
        return 'domain'
    elif '/' in ioc:
        return 'url'
    else:
        return 'file'


def check_crowdstrike_detection(scans):
    if 'CrowdStrike Falcon' in scans and scans['CrowdStrike Falcon']['category'] == 'malicious':
        return 'Yes'
    else:
        return 'No'


def check_sentinelone_detection(scans):
    if 'SentinelOne' in scans and scans['SentinelOne']['category'] == 'malicious':
        return 'Yes'
    else:
        return 'No'


def clean_and_validate_ioc(ioc):
    # Remove leading and trailing whitespace
    cleaned_ioc = ioc.strip()

    # Validate cleaned IOC (basic validation)
    if not cleaned_ioc:
        return None

    ioc_type = determine_ioc_type(cleaned_ioc)
    if ioc_type not in VT_URLS:
        return None

    return cleaned_ioc


def process_ioc(index, ioc):
    ioc = clean_and_validate_ioc(ioc)
    if not ioc:
        return {
            'Index': index,
            'Input': 'Invalid IOC',
            'MD5': None,
            'SHA1': None,
            'SHA256': None,
            'Score': 'Invalid',
            'Microsoft Detection': 'Invalid',
            'Crowdstrike Detection': 'Invalid',
            'SentinelOne Detection': 'Invalid'
        }

    ioc_type = determine_ioc_type(ioc)
    report = get_vt_report(ioc, ioc_type)

    if report:
        if ioc_type == 'file':
            md5 = report['data']['attributes']['md5']
            sha1 = report['data']['attributes']['sha1']
            sha256 = report['data']['attributes']['sha256']
        else:
            md5 = sha1 = sha256 = None

        score = report['data']['attributes']['last_analysis_stats']['malicious']

        microsoft_detection = 'No'
        scans = report['data']['attributes'].get('last_analysis_results', {})
        if 'Microsoft' in scans and scans['Microsoft']['category'] == 'malicious':
            microsoft_detection = 'Yes'

        crowdstrike_detection = check_crowdstrike_detection(scans)
        sentinelone_detection = check_sentinelone_detection(scans)

        return {
            'Index': index,
            'Input': ioc,
            'MD5': md5,
            'SHA1': sha1,
            'SHA256': sha256,
            'Score': score,
            'Microsoft Detection': microsoft_detection,
            'Crowdstrike Detection': crowdstrike_detection,
            'SentinelOne Detection': sentinelone_detection
        }
    else:
        return {
            'Index': index,
            'Input': ioc,
            'MD5': None,
            'SHA1': None,
            'SHA256': None,
            'Score': 'Not found',
            'Microsoft Detection': 'Not found',
            'Crowdstrike Detection': 'Not found',
            'SentinelOne Detection': 'Not found'
        }


def main(num_threads):
    # Read input Excel file
    input_file = 'input.xlsx'
    try:
        df = pd.read_excel(input_file)
    except FileNotFoundError:
        print(f"Error: The file {input_file} does not exist.")
        exit()

    # Flatten the DataFrame into a list of IOCs with their original indices
    ioc_list = [(index, ioc) for index, ioc in enumerate(df.stack().tolist())]

    # Create a list to store output data
    output_data = []

    # Process IOCs with threading
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = {executor.submit(process_ioc, index, ioc): (index, ioc) for index, ioc in ioc_list}
        for future in tqdm(as_completed(futures), total=len(futures)):
            output_data.append(future.result())

    # Sort output data by the original index to maintain input order
    output_data.sort(key=lambda x: x['Index'])

    # Remove the index from the output data
    for entry in output_data:
        del entry['Index']

    # Convert output data to DataFrame
    output_df = pd.DataFrame(output_data)

    # Generate output file name with current date and timestamp
    current_datetime = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"output_{current_datetime}.xlsx"

    # Write output to Excel
    output_df.to_excel(output_file, index=False)

    print(f"Reputation check completed and saved to {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='VirusTotal IOC Checker')
    parser.add_argument('--threads', type=int, default=4, help='Number of threads to use')
    args = parser.parse_args()
    num_threads = args.threads

    # Ensure the number of threads does not exceed the API key limits
    max_possible_threads = len(API_KEYS) * MAX_REQUESTS_PER_MINUTE
    if num_threads > max_possible_threads:
        print(
            f"Warning: Number of threads exceeds the limit based on API keys and rate limit. Using {max_possible_threads} threads instead.")
        num_threads = max_possible_threads

    main(num_threads)
