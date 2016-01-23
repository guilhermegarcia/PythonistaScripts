# See: http://www.devwithimagination.com/2014/05/11/pythonista-dropbox-sync

import webbrowser, os
import dropbox
import hashlib
import json
import difflib
import sys
import logging

# Configuration
# Get your app key and secret from the Dropbox developer website
APP_KEY = 'XXXXXXXXXXX'
APP_SECRET = 'XXXXXXXXX'

# ACCESS_TYPE can be 'dropbox' or 'app_folder' as configured for your app
ACCESS_TYPE = 'app_folder'

# Program, do not edit from here
FINE = 15

SUPPORTED_EXTENSIONS = ['.py', '.pyui', '.txt']
PYTHONISTA_DOC_DIR = os.path.expanduser('~/Documents')
SYNC_FOLDER_NAME = 'dropbox_sync'
SYNC_STATE_FOLDER = os.path.join(PYTHONISTA_DOC_DIR, SYNC_FOLDER_NAME)
SYNC_STATE_FILENAME = 'file.cache.txt'
TOKEN_FILENAME = 'PythonistaDropbox.token'
TOKEN_FILEPATH = os.path.join(SYNC_STATE_FOLDER, TOKEN_FILENAME)

# files that shouldn't be synced
SKIP_FILES = [os.path.join(SYNC_FOLDER_NAME, SYNC_STATE_FILENAME), os.path.join(SYNC_FOLDER_NAME, TOKEN_FILENAME)]

# Method to get the MD5 Hash of the file with the supplied file name.
def getHash(file_name):
	# Open,close, read file and calculate MD5 on its contents
	with open(os.path.join(PYTHONISTA_DOC_DIR, file_name)) as file_to_check:
		# pipe contents of the file through
		return hashlib.md5(file_to_check.read()).hexdigest()

# Method to configure the supplied dropbox session.
# This will use cached OAUTH credentials if they have been stored, otherwise the
# user will be put through the Dropbox authentication process.
def configure_token(dropbox_session):
	if os.path.exists(TOKEN_FILEPATH):
		with open(TOKEN_FILEPATH) as token_file:
			token_key, token_secret = token_file.read().split('|')
		dropbox_session.set_token(token_key,token_secret)
	else:
		setup_new_auth_token(dropbox_session)

# Method to set up a new Dropbox OAUTH token.
# This will take the user through the required steps to authenticate.
def setup_new_auth_token(sess):
	request_token = sess.obtain_request_token()
	url = sess.build_authorize_url(request_token)

	# Make the user sign in and authorize this token
	logging.debug('url: %s', url)
	logging.info('Please visit this website and press the "Allow" button, then hit "Enter" here.')
	webbrowser.open(url)
	raw_input()
	# This will fail if the user didn't visit the above URL and hit 'Allow'
	access_token = sess.obtain_access_token(request_token)
	#save token file
	with open(TOKEN_FILEPATH,'w') as token_file:
		token_file.write("%s|%s" % (access_token.key,access_token.secret) )

def upload(file, details, client, parent_revision):
	logging.log(FINE, 'Trying to upload %s', file)
	details['md5hash'] = getHash(file)
	logging.log(FINE, 'New MD5 hash: %s', details['md5hash'])

	with open(os.path.join(PYTHONISTA_DOC_DIR, file), 'r') as in_file:
		response = client.put_file(file, in_file, False, parent_revision)
	
	logging.debug('Response: %s', response)
	details = update_file_details(details, response)

	logging.log(FINE, 'File %s uploaded to Dropbox', file)

	return details

def download(dest_path, dropbox_metadata, details, client):
	with open(os.path.join(PYTHONISTA_DOC_DIR, dest_path), 'w') as out_file:
		out_file.write(client.get_file(dropbox_metadata['path']).read())

	details['md5hash'] = getHash(dest_path)
	logging.log(FINE, 'New MD5 hash: %s', details['md5hash'])
	return update_file_details(details, dropbox_metadata)

def process_folder(client, dropbox_dir, file_details):

	# Get the metadata for the directory being processed (dropbox_dir).
	# If the directory does not exist on Dropbox it will be created.
	try:
		folder_metadata = client.metadata(dropbox_dir)

		logging.debug('metadata: %s', folder_metadata)
		
	except dropbox.rest.ErrorResponse as error:
		logger.debug(error.status)
		if error.status == 404:
			client.file_create_folder(dropbox_dir)
			folder_metadata = client.metadata(dropbox_dir)
		else:
			logging.exception(error)
			raise error

	# If the directory does not exist locally, create it.
	local_folder = os.path.join(PYTHONISTA_DOC_DIR, dropbox_dir[1:])
	if not os.path.exists(local_folder):
		os.mkdir(local_folder)


	# All the files that have been processed so far in this folder.
	processed_files = []
	# All the directories that exist on Dropbox in the current folder that need to be processed.
	dropbox_dirs = []
	# All the local directories in this current folder that do not exist in Dropbox.
	local_dirs = []

	# Go through the files currently in Dropbox and compare with local
	for file in folder_metadata['contents']:
		dropbox_path = file['path'][1:]
		file_name = file['path'].split('/')[-1]
		
		if file['is_dir'] == False and os.path.splitext(file_name)[1] in (SUPPORTED_EXTENSIONS):

			if not os.path.exists(os.path.join(PYTHONISTA_DOC_DIR, dropbox_path)):
				logging.info('Processing Dropbox file %s (%s)', file['path'], dropbox_path)
				
				try:


					if dropbox_path in file_details:
						# in cache but file no longer locally exists
						details = file_details[dropbox_path]

						logging.info('File %s is in the sync cache and on Dropbox, but no longer exists locally. [Delete From Dropbox (del)|Download File (d)] (Default Delete)', file['path'])

						choice = raw_input()
						if (choice == 'd'):
							download_file = True
						else:
							# Default is 'del'
							download_file = False

							#delete the dropbox copy
							client.file_delete(file['path'])
							file_details.remove(dropbox_path)

					else:
						details = {}
						download_file = True

					if download_file:
						logging.info('Downloading file %s (%s)', file['path'], dropbox_path)
						logging.debug(details)

						details = download(dropbox_path, file, details, client)
						file_details[dropbox_path] = details

					# dealt with this file, don't want to touch it again later
					processed_files.append(file_name)
					write_sync_state(file_details)

				except:
					pass
			else:
				# need to check if we should update this file
				# is this file in our map?
				if dropbox_path in file_details:
					details = file_details[dropbox_path]

					logging.debug('Held details are: %s', details)

					if details['revision'] == file['revision']:
						# same revision
						current_hash = getHash(dropbox_path)

						logging.debug('New hash: %s, Old hash: %s', current_hash, details['md5hash'])

						if current_hash == details['md5hash']:
							logging.log(FINE, 'File "%s" not changed.', dropbox_path)
						else:
							logging.info('File "%s" updated locally, uploading...', dropbox_path)

							details = upload(dropbox_path, details, client, file['rev'])
							file_details[dropbox_path] = details

						processed_files.append(file_name)
					else:
						#different revision
						logging.log(FINE, 'Revision of "%s" changed from %s to %s. ', dropbox_path, details['revision'], file['revision'])

						current_hash = getHash(dropbox_path)

						logging.debug('File %s. New hash: %s, Old hash: %s', dropbox_path, current_hash, details['md5hash'])

						if current_hash == details['md5hash']:
							logging.info('File "%s" updated remotely. Downloading...', dropbox_path)

							details = download(dropbox_path, file, details, client)
							file_details[dropbox_path] = details
						else:
							logging.info('File %s has been updated both locally and on Dropbox. Overwrite [Dropbox Copy (d)|Local Copy (l)| Skip(n)] (Default Skip)', file['path'])
							choice = raw_input()

							if choice in ('d', 'D'):
								logging.log(FINE, 'Overwriting Dropbox Copy of %s', file)
								details = upload(dropbox_path, details, client, file['rev'])
								file_details[dropbox_path] = details
							elif choice in ('l', 'L'):
								logging.log(FINE, 'Overwriting Local Copy of %s', file)
								details = download(dropbox_path, file, details, client)
								file_details[dropbox_path] = details


				else:
					# Not in cache, but exists on dropbox and local, need to prompt user

					logging.info('File %s is not in the sync cache but exists both locally and on dropbox. Overwrite [Dropbox Copy (d)|Local Copy (l) | Skip(n)] (Default Skip)', file['path'])
					choice = raw_input()

					details = {}
					if choice in ('d', 'D'):
						logging.log(FINE, 'Overwriting Dropbox Copy of %s', file)
						details = upload(dropbox_path, details, client, file['rev'])
						file_details[dropbox_path] = details
					elif choice in ('l', 'L'):
						logging.log(FINE, 'Overwriting Local Copy of %s', file)
						details = download(dropbox_path, file, details, client)
						file_details[dropbox_path] = details
					else:
						logging.log(FINE, 'Skipping processing for file %s', file)

				# Finished dealing with this file, update the sync state and mark this file as processed.
				write_sync_state(file_details)
				processed_files.append(file_name)
		elif file['is_dir']:
			dropbox_dirs.append(file['path'])


	# go through the files that are local but not on Dropbox, upload these.
	files = os.listdir(local_folder)
	for file in files:

		full_path = os.path.join(local_folder, file)
		relative_path = os.path.relpath(full_path, PYTHONISTA_DOC_DIR)
		db_path = '/'+relative_path

		if not file in processed_files and not relative_path in (SKIP_FILES) and not os.path.isdir(full_path) and not file.startswith('.'):
			
			filename, file_ext = os.path.splitext(file)
			
			if file_ext in (SUPPORTED_EXTENSIONS):
					
					
				logging.debug('Searching "%s" for "%s"', dropbox_dir, file)
				# this search includes dropbox_dir AND CHILD DIRS!
				search_results = client.search(dropbox_dir, file)
				
				logging.debug(search_results)
				
				found = False
				for single_result in search_results:
					if single_result['path'] == db_path:
						found = True

				if found:
					logging.warning("File found on Dropbox, this shouldn't happen! Skipping %s...", file)
				else:
					logging.debug(relative_path)

					if relative_path in file_details:
						details = file_details[relative_path]
					else:
						details = {}
					logging.debug(details)

					details = upload(relative_path, details, client, None )
					file_details[relative_path] = details
					write_sync_state(file_details)
				
			else:
				logging.debug("Skipping extension %s", file_ext)

		elif not db_path in dropbox_dirs and os.path.isdir(full_path) and not file.startswith('.') and not file == SYNC_STATE_FOLDER:
			local_dirs.append(db_path)


	#process the directories
	for folder in dropbox_dirs:
		logging.debug('Processing dropbox dir %s from %s', folder, dropbox_dir)
		process_folder(client, folder, file_details)

	for folder in local_dirs:
		logging.debug('Processing local dir %s from %s', folder, dropbox_dir)
		if folder[1:] not in SKIP_FILES:
			process_folder(client, folder, file_details)

def update_file_details(file_details, dropbox_metadata):
	for key in 'revision rev modified path'.split():
		file_details[key] = dropbox_metadata[key]
	return file_details

def write_sync_state(file_details):
	# Write sync state file
	sync_status_file = os.path.join(SYNC_STATE_FOLDER, SYNC_STATE_FILENAME)

	logging.debug('Writing sync state to %s', sync_status_file)

	with open(sync_status_file, 'w') as output_file:
		json.dump(file_details, output_file)

def main():

	# Process any supplied arguments
	log_level = 'INFO'
	
	for argument in sys.argv:
		if argument.lower() == '-v':
			log_level = 'FINE'
		elif argument.lower() == '-vv':
			log_level = 'DEBUG'
			
	# configure logging
	log_format = "%(message)s"
	
	logging.addLevelName(FINE, 'FINE')
	for handler in logging.getLogger().handlers:
		logging.getLogger().removeHandler(handler)
	logging.basicConfig(format=log_format, level=log_level)
	

	# Load the current sync status file, if it exists.
	sync_status_file = os.path.join(SYNC_STATE_FOLDER, SYNC_STATE_FILENAME)

	if not os.path.exists(SYNC_STATE_FOLDER):
		os.mkdir(SYNC_STATE_FOLDER)
	if os.path.exists(sync_status_file):
		with open(sync_status_file, 'r') as input_file:
			file_details = json.load(input_file)
	else:
		file_details = {}

	logging.debug('File Details: %s', file_details)
		
		
	logging.info('Begin Dropbox sync')

	#configure dropbox
	sess = dropbox.session.DropboxSession(APP_KEY, APP_SECRET, ACCESS_TYPE)
	configure_token(sess)
	client = dropbox.client.DropboxClient(sess)

	logging.info('linked account: %s', client.account_info()['display_name'])

	process_folder(client, '/', file_details)

	# Write sync state file
	write_sync_state(file_details)


if __name__ == "__main__":
	main()
	logging.info('Dropbox sync done!')