import os
import json
import pickle
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import requests
from datetime import datetime
import pytz

# Constants for Stock Balance
SPECIFICATION_SHEET_ID = os.environ.get('SPECIFICATION_SHEET_ID')
INVENTORY_SHEET_ID = os.environ.get('INVENTORY_SHEET_ID')
if not INVENTORY_SHEET_ID:
    raise ValueError("INVENTORY_SHEET_ID environment variable not set")
if not SPECIFICATION_SHEET_ID:
    raise ValueError("SPECIFICATION_SHEET_ID environment variable not set")
    
STOCK_SHEET_NAME = 'balance'
STOCK_RANGE = 'A1:P3'  # Range covers A-P columns (Specification through TOTAL including Gizzard)

INVENTORY_SHEET_NAME = 'summary'  # The sheet name from the inventory tracking spreadsheet
INVENTORY_RANGE = 'A:BZ'  # Get all columns since we're finding them by name (extends beyond Z for 53+ columns)

PARTS_SHEET_NAME = 'parts_balance'
PARTS_RANGE = 'A1:H3'  # Adjust range to cover all parts data

SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
SERVICE_ACCOUNT_FILE = 'service-account.json'

# Set up data directory for state persistence
DATA_DIR = os.getenv('GITHUB_WORKSPACE', os.getcwd())
os.makedirs(DATA_DIR, exist_ok=True)

# Separate state files for stock, parts, and differences
STOCK_STATE_FILE = os.path.join(DATA_DIR, 'previous_stock_state.pickle')
PARTS_STATE_FILE = os.path.join(DATA_DIR, 'previous_parts_state.pickle')
WHOLE_CHICKEN_DIFF_STATE_FILE = os.path.join(DATA_DIR, 'previous_whole_chicken_diff_state.pickle')
GIZZARD_DIFF_STATE_FILE = os.path.join(DATA_DIR, 'previous_gizzard_diff_state.pickle')

class APIError(Exception):
    """Custom exception for API related errors."""
    pass

def get_service():
    """Create and return Google Sheets service object."""
    try:
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        return build('sheets', 'v4', credentials=credentials)
    except Exception as e:
        print(f"Error initializing Google Sheets service: {str(e)}")
        raise APIError("Failed to initialize Google Sheets service")

def get_sheet_data(service, sheet_name, range_name):
    """Fetch data from Google Sheet."""
    print(f"Fetching data from sheet {sheet_name}...")
    try:
        sheet = service.spreadsheets()
        result = sheet.values().get(
            spreadsheetId=SPECIFICATION_SHEET_ID,
            range=f'{sheet_name}!{range_name}'
        ).execute()
        data = result.get('values', [])
        
        # Validate data structure
        min_rows = 2  # Both stock and parts sheets now have 2 rows
        if not data or len(data) < min_rows:
            raise APIError(f"Invalid data structure received from Google Sheets for {sheet_name}")
            
        print(f"Data fetched successfully from {sheet_name}")
        return data
    except HttpError as e:
        print(f"Google Sheets API error: {str(e)}")
        raise APIError(f"Failed to fetch data from Google Sheets for {sheet_name}")
    except Exception as e:
        print(f"Unexpected error fetching sheet data: {str(e)}")
        raise APIError(f"Unexpected error while fetching data from {sheet_name}")

def load_previous_state(state_file):
    """Load previous state from file."""
    print(f"Checking for previous state file {state_file}")
    try:
        if os.path.exists(state_file):
            print(f"Loading previous state from {state_file}")
            with open(state_file, 'rb') as f:
                data = pickle.load(f)
                # Check if this is a difference state file (contains single value)
                if 'diff_state' in state_file:
                    # Difference state files contain single numeric values
                    if not isinstance(data, (int, float)) and data is not None:
                        print("Invalid difference state data found, treating as no previous state")
                        return None
                else:
                    # Stock and parts state files expect 2 rows
                    min_rows = 2
                    if not data or len(data) < min_rows:
                        print("Invalid state data found, treating as no previous state")
                        return None
                print("Previous state loaded successfully")
                return data
        print("No previous state file found")
        return None
    except Exception as e:
        print(f"Error loading previous state: {str(e)}")
        return None

def save_current_state(state, state_file):
    """Save current state to file."""
    # Check if this is a difference state file (contains single value)
    if 'diff_state' in state_file:
        # Difference state files contain single numeric values
        if not isinstance(state, (int, float)) and state is not None:
            print("Invalid difference state data, skipping save")
            return
    else:
        # Stock and parts state files expect 2 rows
        min_rows = 2
        if not state or len(state) < min_rows:
            print("Invalid state data, skipping save")
            return
        
    print(f"Saving current state to {state_file}")
    try:
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        with open(state_file, 'wb') as f:
            pickle.dump(state, f)
        print(f"State saved successfully to {state_file}")
    except Exception as e:
        print(f"Error saving state: {str(e)}")
        raise APIError("Failed to save state file")

def detect_stock_changes(previous_data, current_data):
    """Detect changes between previous and current stock data."""
    if not previous_data:
        print("No previous stock data available")
        return []
    
    try:
        changes = []
        # Skip header row and compare the balance row
        prev_row = previous_data[1]
        curr_row = current_data[1]
        headers = current_data[0]
        
        # Validate data lengths
        if len(prev_row) != len(curr_row) or len(headers) != len(curr_row):
            print(f"Data length mismatch - Previous: {len(prev_row)}, Current: {len(curr_row)}, Headers: {len(headers)}")
            print("Resetting previous stock state file to match new structure.")
            save_current_state(current_data, STOCK_STATE_FILE)
            return []
        
        print("\nComparing stock states...")
        
        # Compare each value and convert to same type before comparison
        for i in range(len(prev_row)):
            # Convert both values to strings for comparison to avoid type mismatches
            prev_val = str(prev_row[i]).strip()
            curr_val = str(curr_row[i]).strip()
            
            if prev_val != curr_val:
                changes.append((headers[i], prev_row[i], curr_row[i]))
                print(f"Change detected in {headers[i]}")
        
        if changes:
            print(f"Detected {len(changes)} stock changes")
        else:
            print("No changes detected in stock balance")
        return changes
    except Exception as e:
        print(f"Error detecting stock changes: {str(e)}")
        raise APIError("Failed to compare stock states")

def detect_parts_changes(previous_data, current_data):
    """Detect changes between previous and current parts data."""
    if not previous_data:
        print("No previous parts data available")
        return []
    
    try:
        changes = []
        # Get part headers from row 1 (starting from column B which is index 1)
        part_headers = []
        if len(current_data) > 0 and len(current_data[0]) > 1:
            part_headers = current_data[0][1:]  # Skip "Parts Type" column
        
        # Get previous values from row 2 (starting from column B which is index 1)
        prev_values = []
        if len(previous_data) > 1 and len(previous_data[1]) > 1:
            prev_values = previous_data[1][1:]  # Skip "Balance" label
        
        # Get current values from row 2 (starting from column B which is index 1)
        curr_values = []
        if len(current_data) > 1 and len(current_data[1]) > 1:
            curr_values = current_data[1][1:]  # Skip "Balance" label
        
        # Validate data structure
        if len(part_headers) != len(curr_values):
            print(f"Warning: Mismatch between parts ({len(part_headers)}) and values ({len(curr_values)})")
            # Use the shorter length for comparison
            compare_length = min(len(part_headers), len(curr_values))
            # Trim the arrays to the same length
            part_headers = part_headers[:compare_length]
            curr_values = curr_values[:compare_length]
            prev_values = prev_values[:compare_length] if len(prev_values) > compare_length else prev_values
        
        # If previous values array is shorter than current, pad it
        if len(prev_values) < len(curr_values):
            print(f"Warning: Previous values array ({len(prev_values)}) shorter than current ({len(curr_values)})")
            # Pad with empty strings
            prev_values = prev_values + [''] * (len(curr_values) - len(prev_values))
        # If previous values array is longer, trim it
        elif len(prev_values) > len(curr_values):
            print(f"Warning: Previous values array ({len(prev_values)}) longer than current ({len(curr_values)})")
            prev_values = prev_values[:len(curr_values)]
            
        print("\nComparing parts states...")
        
        # Compare each value and detect changes
        for i in range(len(part_headers)):
            if i >= len(prev_values) or i >= len(curr_values):
                print(f"Warning: Index {i} out of bounds. Skipping comparison.")
                continue
                
            # Convert both values to strings for comparison to avoid type mismatches
            prev_val = str(prev_values[i]).strip()
            curr_val = str(curr_values[i]).strip()
            
            if prev_val != curr_val:
                changes.append((part_headers[i], prev_values[i], curr_values[i]))
                print(f"Change detected in {part_headers[i]}")
        
        # Total is now included in the part headers and values, so no separate check needed
        
        if changes:
            print(f"Detected {len(changes)} parts changes")
        else:
            print("No changes detected in parts weights")
        return changes
    except Exception as e:
        print(f"Error detecting parts changes: {str(e)}")
        print("Attempting to reset parts state file for next run...")
        # Save current state to recover from this error
        save_current_state(current_data, PARTS_STATE_FILE)
        print("Parts state file updated with current data. Next run should work correctly.")
        # Return empty changes to avoid further errors
        return []

def detect_chicken_difference_changes(previous_chicken_diff, current_chicken_diff):
    """Detect changes between previous and current whole chicken inventory balance difference."""
    if previous_chicken_diff is None:
        print("No previous whole chicken difference data available")
        return []
    
    try:
        changes = []
        
        print("\nComparing whole chicken difference states...")
        
        if current_chicken_diff is not None and previous_chicken_diff != current_chicken_diff:
            changes.append(('Whole Chicken Balance Difference', previous_chicken_diff, current_chicken_diff))
            print(f"Change detected in Whole Chicken Balance Difference")
        
        if changes:
            print(f"Detected {len(changes)} whole chicken difference changes")
        else:
            print("No changes detected in whole chicken inventory balance difference")
        return changes
    except Exception as e:
        print(f"Error detecting whole chicken difference changes: {str(e)}")
        raise APIError("Failed to compare whole chicken difference states")

def detect_gizzard_difference_changes(previous_gizzard_diff, current_gizzard_diff):
    """Detect changes between previous and current gizzard inventory balance difference."""
    if previous_gizzard_diff is None:
        print("No previous gizzard difference data available")
        return []
    
    try:
        changes = []
        
        print("\nComparing gizzard difference states...")
        
        if current_gizzard_diff is not None:
            # Use small tolerance for floating point comparison
            if abs(previous_gizzard_diff - current_gizzard_diff) >= 0.01:
                changes.append(('Gizzard Balance Difference', previous_gizzard_diff, current_gizzard_diff))
                print(f"Change detected in Gizzard Balance Difference")
        
        if changes:
            print(f"Detected {len(changes)} gizzard difference changes")
        else:
            print("No changes detected in gizzard inventory balance difference")
        return changes
    except Exception as e:
        print(f"Error detecting gizzard difference changes: {str(e)}")
        raise APIError("Failed to compare gizzard difference states")

def get_inventory_balance(service):
    """Fetch and calculate inventory balance from the inflow/release sheet."""
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=INVENTORY_SHEET_ID,
            range=f'{INVENTORY_SHEET_NAME}!{INVENTORY_RANGE}'
        ).execute()
        
        data = result.get('values', [])
        if not data:
            print("No data found in inventory sheet")
            return None
            
        # Get the header row to find the column indices
        if len(data) < 2:  # Need at least header row and one data row
            print("Not enough rows in inventory sheet")
            return None
            
        headers = data[0]
        try:
            balance_col_index = headers.index('whole_chicken_quantity_stock_balance')
            year_month_col_index = headers.index('year_month')
        except ValueError as e:
            print(f"Could not find required column in inventory sheet: {str(e)}")
            return None
            
        # Get current year-month in YYYY-MM format
        current_date = datetime.now(pytz.UTC).astimezone(pytz.timezone('Africa/Lagos'))
        current_year_month = current_date.strftime('%Y-%m')
        
        # Find the row for the current month
        data_rows = data[1:]  # Skip header row
        current_month_row = None
        
        for row in data_rows:
            if len(row) > year_month_col_index and row[year_month_col_index] == current_year_month:
                current_month_row = row
                break
        
        if not current_month_row:
            print(f"Warning: No data found for current month ({current_year_month})")
            # Sort by year_month in descending order to get the most recent record as fallback
            sorted_data = sorted(data_rows, 
                               key=lambda x: x[year_month_col_index] if len(x) > year_month_col_index else '', 
                               reverse=True)
            if sorted_data:
                current_month_row = sorted_data[0]
                print(f"Using most recent available data from {current_month_row[year_month_col_index]}")
            else:
                return None
        
        if len(current_month_row) > balance_col_index:
            try:
                balance = float(current_month_row[balance_col_index])
                return balance
            except (ValueError, TypeError):
                print("Invalid balance value in inventory sheet")
                return None
        return None
    except Exception as e:
        print(f"Error fetching inventory balance: {str(e)}")
        return None

def get_gizzard_inventory_balance(service):
    """Fetch and calculate gizzard weight balance from the inventory sheet."""
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=INVENTORY_SHEET_ID,
            range=f'{INVENTORY_SHEET_NAME}!{INVENTORY_RANGE}'
        ).execute()
        
        data = result.get('values', [])
        if not data:
            print("No data found in inventory sheet for gizzard")
            return None
            
        # Get the header row to find the column indices
        if len(data) < 2:  # Need at least header row and one data row
            print("Not enough rows in inventory sheet for gizzard")
            return None
            
        headers = data[0]
        try:
            gizzard_col_index = headers.index('gizzard_weight_stock_balance')
            year_month_col_index = headers.index('year_month')
        except ValueError as e:
            print(f"Could not find required column in inventory sheet for gizzard: {str(e)}")
            return None
            
        # Get current year-month in YYYY-MM format
        current_date = datetime.now(pytz.UTC).astimezone(pytz.timezone('Africa/Lagos'))
        current_year_month = current_date.strftime('%Y-%m')
        
        # Find the row for the current month
        data_rows = data[1:]  # Skip header row
        current_month_row = None
        
        for row in data_rows:
            if len(row) > year_month_col_index and row[year_month_col_index] == current_year_month:
                current_month_row = row
                break
        
        if not current_month_row:
            print(f"Warning: No data found for current month ({current_year_month}) for gizzard")
            # Sort by year_month in descending order to get the most recent record as fallback
            sorted_data = sorted(data_rows, 
                               key=lambda x: x[year_month_col_index] if len(x) > year_month_col_index else '', 
                               reverse=True)
            if sorted_data:
                current_month_row = sorted_data[0]
                print(f"Using most recent available data from {current_month_row[year_month_col_index]} for gizzard")
            else:
                return None
        
        if len(current_month_row) > gizzard_col_index:
            try:
                balance = float(current_month_row[gizzard_col_index])
                return balance
            except (ValueError, TypeError):
                print("Invalid gizzard balance value in inventory sheet")
                return None
        return None
    except Exception as e:
        print(f"Error fetching gizzard inventory balance: {str(e)}")
        return None

def calculate_total_pieces(stock_data):
    """Calculate total pieces from stock data, excluding Gizzard."""
    try:
        headers = stock_data[0]
        values = stock_data[1]
        total = 0
        
        for i in range(len(headers)):
            header = headers[i].lower()
            if header != 'specification' and header != 'gizzard' and header != 'total':
                try:
                    val = values[i]
                    if str(val).strip().replace(',', '').isdigit():
                        total += int(float(val))
                except (ValueError, TypeError):
                    continue
        return total
    except Exception as e:
        print(f"Error calculating total pieces: {str(e)}")
        return None

def calculate_current_differences(stock_data, inventory_balance, gizzard_inventory_balance):
    """Calculate current inventory balance differences."""
    try:
        # Calculate whole chicken difference
        total_pieces = calculate_total_pieces(stock_data)
        whole_chicken_diff = None
        if total_pieces is not None and inventory_balance is not None:
            whole_chicken_diff = int(total_pieces - inventory_balance)
        
        # Calculate gizzard difference
        headers = stock_data[0]
        values = stock_data[1]
        current_gizzard_weight = 0
        gizzard_diff = None
        
        for i in range(len(headers)):
            if headers[i].lower() == 'gizzard':
                try:
                    current_gizzard_weight = float(values[i])
                    break
                except (ValueError, TypeError):
                    current_gizzard_weight = 0
                    break
        
        if current_gizzard_weight > 0 and gizzard_inventory_balance is not None:
            gizzard_diff = current_gizzard_weight - gizzard_inventory_balance
        
        return whole_chicken_diff, gizzard_diff
    except Exception as e:
        print(f"Error calculating current differences: {str(e)}")
        return None, None

def format_stock_section(stock_changes, stock_data, inventory_balance=None, gizzard_inventory_balance=None):
    """Format the stock section of the alert message."""
    section = ""
    
    # Add stock changes if any
    if stock_changes:
        section += "*Stock Balance Changes:*\n"
        for spec, old_val, new_val in stock_changes:
            # Capitalize first letter of specification
            spec = spec.title()
            
            # Check if this is a weight-based value (like Gizzard)
            is_weight = spec.lower() == "gizzard"
            
            # Try to convert values to numbers and append appropriate units
            try:
                if is_weight:
                    # Handle weight values (in kg)
                    old_val_num = float(old_val) if str(old_val).strip().replace('.', '', 1).isdigit() else None
                    new_val_num = float(new_val) if str(new_val).strip().replace('.', '', 1).isdigit() else None
                    
                    if old_val_num is not None:
                        old_val_str = f"{old_val_num:,.2f} kg"
                    else:
                        old_val_str = str(old_val)
                        
                    if new_val_num is not None:
                        new_val_str = f"{new_val_num:,.2f} kg"
                    else:
                        new_val_str = str(new_val)
                else:
                    # Handle piece-based values
                    old_val_num = float(old_val) if str(old_val).strip().replace(',', '').isdigit() else None
                    new_val_num = float(new_val) if str(new_val).strip().replace(',', '').isdigit() else None
                    
                    if old_val_num is not None:
                        old_suffix = " piece" if old_val_num == 1 else " pieces"
                        old_val_str = f"{old_val_num:,.0f}{old_suffix}"
                    else:
                        old_val_str = str(old_val)
                        
                    if new_val_num is not None:
                        new_suffix = " piece" if new_val_num == 1 else " pieces"
                        new_val_str = f"{new_val_num:,.0f}{new_suffix}"
                    else:
                        new_val_str = str(new_val)
                
                section += f"• {spec}: {old_val_str} → {new_val_str}\n"
            except (ValueError, TypeError):
                section += f"• {spec}: {old_val} → {new_val}\n"
        section += "\n"
    
    # Always add current stock levels
    section += "*Current Stock Levels:*\n"
    headers = stock_data[0]
    values = stock_data[1]
    total_pieces = 0
    current_gizzard_weight = 0
    
    for i in range(len(headers)):
        # Skip 'Specification' header if it exists
        if headers[i].lower() != 'specification':
            try:
                # Capitalize first letter of header
                header = headers[i].title()
                
                # Check if this is a weight-based value (like Gizzard)
                is_weight = header.lower() == "gizzard"
                
                # Try to convert value to number and format appropriately
                val = values[i]
                if is_weight:
                    # Handle weight values (in kg)
                    if str(val).strip().replace('.', '', 1).isdigit():
                        current_gizzard_weight = float(val)
                        formatted_val = f"{current_gizzard_weight:,.2f} kg"
                    else:
                        formatted_val = str(val)
                else:
                    # Handle piece-based values
                    if str(val).strip().replace(',', '').isdigit():
                        total_pieces = int(float(val)) if header.lower() == 'total' else total_pieces
                        total_val = int(float(val))
                        bags = total_val // 20
                        remaining_pieces = total_val % 20
                        
                        # Use proper singular/plural forms
                        bags_text = "1 bag" if bags == 1 else f"{bags:,} bags"
                        pieces_text = "1 piece" if remaining_pieces == 1 else f"{remaining_pieces} pieces"
                        
                        if bags > 0 and remaining_pieces > 0:
                            formatted_val = f"{bags_text}, {pieces_text}"
                        elif bags > 0:
                            formatted_val = bags_text
                        else:
                            formatted_val = pieces_text
                    else:
                        formatted_val = str(val)
                section += f"• {header}: {formatted_val}\n"
            except (ValueError, TypeError):
                section += f"• {headers[i].title()}: {values[i]}\n"
    
    # Add inventory balance comparison if available
    if inventory_balance is not None and total_pieces > 0:
        section += "\n*Whole Chicken Stock Balance Comparison:*\n"
        difference = int(total_pieces - inventory_balance)  # Convert to integer
        if difference == 0:
            section += "✅ Whole chicken stock balance matches inventory records\n"
        else:
            section += f"⚠️ Whole chicken stock balance discrepancy detected:\n"
            section += f"• Specification Sheet Total: {total_pieces:,} pieces\n"
            section += f"• Inventory Records Total: {int(inventory_balance):,} pieces\n"  # Convert to integer
            section += f"• Difference: {abs(difference):,} pieces {'more' if difference > 0 else 'less'} in specification sheet\n"
    
    # Add gizzard inventory balance comparison if available
    if gizzard_inventory_balance is not None and current_gizzard_weight > 0:
        section += "\n*Gizzard Stock Balance Comparison:*\n"
        difference = current_gizzard_weight - gizzard_inventory_balance
        if abs(difference) < 0.01:  # Allow for small floating point differences
            section += "✅ Gizzard stock balance matches inventory records\n"
        else:
            section += f"⚠️ Gizzard stock balance discrepancy detected:\n"
            section += f"• Specification Sheet Gizzard: {current_gizzard_weight:,.2f} kg\n"
            section += f"• Inventory Records Gizzard: {gizzard_inventory_balance:,.2f} kg\n"
            section += f"• Difference: {abs(difference):,.2f} kg {'more' if difference > 0 else 'less'} in specification sheet\n"
    
    return section

def format_parts_section(parts_changes, parts_data):
    """Format the parts section of the alert message."""
    section = ""
    
    # Add parts changes if any
    if parts_changes:
        section += "*Parts Weight Changes:*\n"
        for part, old_val, new_val in parts_changes:
            # Capitalize first letter of part name
            part = part.title()
            
            # Try to convert values to numbers with weight suffix
            try:
                # Check if values are numeric
                if str(old_val).strip().replace('.', '', 1).isdigit():
                    old_val_num = float(old_val)
                    # Use "kg" for all weights as it's a unit, not a count
                    old_val_str = f"{old_val_num:,.2f} kg"
                else:
                    old_val_str = str(old_val)
                    
                if str(new_val).strip().replace('.', '', 1).isdigit():
                    new_val_num = float(new_val)
                    new_val_str = f"{new_val_num:,.2f} kg"
                else:
                    new_val_str = str(new_val)
                    
                section += f"• {part}: {old_val_str} → {new_val_str}\n"
            except (ValueError, TypeError):
                section += f"• {part}: {old_val} → {new_val}\n"
        section += "\n"
    
    # Always add current parts weights
    section += "*Current Parts Weights:*\n"
    
    # Get part headers from row 1 (starting from column B which is index 1)
    part_headers = []
    if len(parts_data) > 0 and len(parts_data[0]) > 1:
        part_headers = parts_data[0][1:]  # Skip "Parts Type" column
    
    # Get values from row 2 (starting from column B which is index 1)
    values = []
    if len(parts_data) > 1 and len(parts_data[1]) > 1:
        values = parts_data[1][1:]  # Skip "Balance" label in row 2
    
    # Map values to headers
    for i in range(min(len(part_headers), len(values))):
        try:
            # Format weight values
            val = values[i]
            # Capitalize part name
            part_name = part_headers[i].title()
            
            if str(val).strip().replace('.', '', 1).isdigit():
                # "kg" is always singular as it's a unit
                formatted_val = f"{float(val):,.2f} kg"
            else:
                formatted_val = str(val)
            section += f"• {part_name}: {formatted_val}\n"
        except (ValueError, TypeError, IndexError) as e:
            print(f"Error formatting part {i}: {str(e)}")
            # Ensure part name is capitalized even in error case
            part_name = part_headers[i].title() if i < len(part_headers) else 'Unknown'
            section += f"• {part_name}: {values[i] if i < len(values) else 'N/A'}\n"
    
    return section

def send_combined_alert(webhook_url, stock_changes, stock_data, parts_changes, parts_data, inventory_balance=None, gizzard_inventory_balance=None, chicken_difference_changes=None, gizzard_difference_changes=None):
    """Send combined alert to Google Space."""
    try:
        # Only proceed if there are actual changes
        if not stock_changes and not parts_changes and not chicken_difference_changes and not gizzard_difference_changes:
            print("No changes detected in stock, parts, or differences. No alert needed.")
            return True
        
        message = "🔔 *Kaduna Inventory Changes Detected*\n\n"
        print("Preparing combined changes message")
        
        # Add difference changes section if there are difference changes
        difference_changes = []
        if chicken_difference_changes:
            difference_changes.extend(chicken_difference_changes)
        if gizzard_difference_changes:
            difference_changes.extend(gizzard_difference_changes)
            
        if difference_changes:
            message += "*Inventory Balance Difference Changes:*\n"
            for change_type, old_val, new_val in difference_changes:
                if 'Chicken' in change_type:
                    # Format whole chicken differences as pieces
                    old_suffix = " piece" if abs(old_val) == 1 else " pieces"
                    new_suffix = " piece" if abs(new_val) == 1 else " pieces"
                    old_val_str = f"{old_val:,}{old_suffix}"
                    new_val_str = f"{new_val:,}{new_suffix}"
                else:
                    # Format gizzard differences as kg
                    old_val_str = f"{old_val:,.2f} kg"
                    new_val_str = f"{new_val:,.2f} kg"
                message += f"• {change_type}: {old_val_str} → {new_val_str}\n"
            message += "\n"
        
        # Always add stock section (shows current balances regardless of what triggered alert)
        message += format_stock_section(stock_changes, stock_data, inventory_balance, gizzard_inventory_balance)
        message += "\n"
        
        # Always add parts section (shows current weights regardless of what triggered alert)
        message += format_parts_section(parts_changes, parts_data)
        
        # Get current time in WAT
        wat_tz = pytz.timezone('Africa/Lagos')
        current_time = datetime.now(pytz.UTC).astimezone(wat_tz)
        message += f"\n_Updated at: {current_time.strftime('%Y-%m-%d %I:%M:%S %p')} WAT_"
        
        payload = {
            "text": message
        }
        
        print("Sending webhook request...")
        response = requests.post(webhook_url, json=payload, timeout=10)  # Add timeout
        response.raise_for_status()  # Raise exception for bad status codes
        print(f"Webhook response status: {response.status_code}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error sending alert to Google Space: {str(e)}")
        return False

def main():
    try:
        # Get webhook URL from environment variable
        webhook_url = os.environ.get('SPACE_WEBHOOK_URL')
        if not webhook_url:
            raise ValueError("SPACE_WEBHOOK_URL environment variable not set")
        print("Webhook URL configured")

        # Initialize the Sheets API service
        print("Initializing Google Sheets service...")
        service = get_service()
        
        # Get current stock data
        stock_data = get_sheet_data(service, STOCK_SHEET_NAME, STOCK_RANGE)
        
        # Get current parts data
        parts_data = get_sheet_data(service, PARTS_SHEET_NAME, PARTS_RANGE)
        
        # Get inventory balance for comparison
        inventory_balance = get_inventory_balance(service)
        
        # Get gizzard inventory balance for comparison
        gizzard_inventory_balance = get_gizzard_inventory_balance(service)
        
        # Load previous states
        previous_stock_data = load_previous_state(STOCK_STATE_FILE)
        previous_parts_data = load_previous_state(PARTS_STATE_FILE)
        previous_chicken_diff = load_previous_state(WHOLE_CHICKEN_DIFF_STATE_FILE)
        previous_gizzard_diff = load_previous_state(GIZZARD_DIFF_STATE_FILE)
        
        # Calculate current differences
        current_chicken_diff, current_gizzard_diff = calculate_current_differences(stock_data, inventory_balance, gizzard_inventory_balance)
        
        # Initialize flags for state updates
        stock_state_needs_update = True
        parts_state_needs_update = True
        chicken_diff_state_needs_update = True
        gizzard_diff_state_needs_update = True
        
        # Check for changes in stock data
        stock_changes = []
        if not previous_stock_data:
            print("No previous stock state found, initializing stock state file...")
        else:
            print("Checking for stock changes...")
            stock_changes = detect_stock_changes(previous_stock_data, stock_data)
        
        # Check for changes in parts data
        parts_changes = []
        if not previous_parts_data:
            print("No previous parts state found, initializing parts state file...")
        else:
            print("Checking for parts changes...")
            parts_changes = detect_parts_changes(previous_parts_data, parts_data)
        
        # Check for changes in whole chicken inventory balance differences
        chicken_difference_changes = []
        if previous_chicken_diff is None:
            print("No previous whole chicken difference state found, initializing state file...")
        else:
            print("Checking for whole chicken difference changes...")
            chicken_difference_changes = detect_chicken_difference_changes(previous_chicken_diff, current_chicken_diff)
        
        # Check for changes in gizzard inventory balance differences
        gizzard_difference_changes = []
        if previous_gizzard_diff is None:
            print("No previous gizzard difference state found, initializing state file...")
        else:
            print("Checking for gizzard difference changes...")
            gizzard_difference_changes = detect_gizzard_difference_changes(previous_gizzard_diff, current_gizzard_diff)
        
        # Send combined alert if there are any changes
        if stock_changes or parts_changes or chicken_difference_changes or gizzard_difference_changes:
            print("Changes detected, sending combined alert...")
            if send_combined_alert(webhook_url, stock_changes, stock_data, parts_changes, parts_data, inventory_balance, gizzard_inventory_balance, chicken_difference_changes, gizzard_difference_changes):
                print("Alert sent successfully, updating state files...")
            else:
                print("Failed to send alert, but will still update state files...")
        else:
            print("No changes detected in stock, parts, or differences, updating state files...")
        
        # Always update all state files at the end
        if stock_state_needs_update:
            save_current_state(stock_data, STOCK_STATE_FILE)
        if parts_state_needs_update:
            save_current_state(parts_data, PARTS_STATE_FILE)
        if chicken_diff_state_needs_update:
            save_current_state(current_chicken_diff, WHOLE_CHICKEN_DIFF_STATE_FILE)
        if gizzard_diff_state_needs_update:
            save_current_state(current_gizzard_diff, GIZZARD_DIFF_STATE_FILE)

    except APIError as e:
        print(f"API Error: {str(e)}")
        # Don't exit with error to avoid GitHub Actions failure
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        # Don't exit with error to avoid GitHub Actions failure

if __name__ == '__main__':
    main() 