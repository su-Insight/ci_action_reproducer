import csv
import os
import logging
import pandas as pd

class BuildCSVHandler:
    def __init__(self, output_csv):
        self.output_csv = output_csv
        self.fieldnames = [
            'repository_name', 'run_id', 'status', 'exception',
            'new_repository', 'new_run_id', 'conclusion'
        ]
        self.TIMEOUT = 10

    def save(self, builds_info):
        """Save new build information to CSV, avoiding duplicates based on run_id"""
        if not builds_info:
            return  # Skip if no new builds
        # with FileLock(lock_path + '.lock', timeout=TIMEOUT):
        # Load existing run_ids
        existing_ids = set()
        if os.path.exists(self.output_csv):
            try:
                df_existing = pd.read_csv(self.output_csv, usecols=['run_id'])
                existing_ids = set(df_existing['run_id'].astype(str))
            except Exception as e:
                logging.error(f"Error reading existing run IDs from {self.output_csv}: {e}")

        # Filter out duplicate builds
        new_builds = [b for b in builds_info if str(b['run_id']) not in existing_ids]

        if new_builds:
            file_exists = os.path.exists(self.output_csv) and os.path.getsize(self.output_csv) > 0
            with open(self.output_csv, mode='a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=self.fieldnames)
                if not file_exists:
                    writer.writeheader()
                writer.writerows(new_builds)
            logging.info(f"✅ {len(new_builds)} new build(s) added to {self.output_csv}.")
        else:
            logging.info("⚠️ No new builds to add, skipping file write.")

    def save_header(self):
        """Save CSV header only if the file is empty or does not exist"""
        if os.path.exists(self.output_csv) and os.path.getsize(self.output_csv) > 0:
            with open(self.output_csv, mode='r', encoding='utf-8') as file:
                first_line = file.readline().strip()
                if first_line == ','.join(self.fieldnames):
                    logging.info(f"Header already exists in {self.output_csv}. Skipping header write.")
                    return

        with open(self.output_csv, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=self.fieldnames)
            writer.writeheader()
        logging.info(f"CSV header saved to {self.output_csv}")

    def read_basic_build_info_as_dict(self):
        """
        Read CSV file and return only repository_name, run_id, status columns
        as a list of dictionaries.
        """
        columns_to_keep = ['repository_name', 'run_id', 'status']
        result = []

        if not os.path.exists(self.output_csv):
            return result  # File doesn't exist, return empty list

        with open(self.output_csv, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Keep only the requested columns
                filtered_row = {col: row.get(col, '') for col in columns_to_keep}
                result.append(filtered_row)

        return result
