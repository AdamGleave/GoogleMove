import logging
import os
import traceback

from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

CLIENT_SECRET_FILE = 'downloaded_credentials_file.json'
SCOPES = ['https://www.googleapis.com/auth/drive']
Q_FOLDERSONLY = "mimeType = 'application/vnd.google-apps.folder'"
logger = logging.getLogger(__name__)

def authenticate():
    """
    Authenticate to create initial credentials, and store them in token.json
    """
    logger.warning('You need to authenticate for the first time.\n'
                   'If this is not your first time, you probably are missing a refresh token.')
    flow = InstalledAppFlow.from_client_secrets_file(
        CLIENT_SECRET_FILE, SCOPES)
    return flow.run_local_server(port=8083)


def get_creds_from_token_file():
    with open('token.json', 'r', encoding='utf-8') as infile:
        if 'refresh_token' not in infile.read():
            logger.warning("WARNING: No refresh token found in this file. You should remove your project's permissions"
                           "to your account and re-authenticate to create a token file which has one.")
    return Credentials.from_authorized_user_file('token.json', SCOPES)


def get_creds():
    # https://developers.google.com/drive/api/v3/quickstart/python
    creds = None
    if os.path.exists('token.json'):
        creds = get_creds_from_token_file()
    if creds and creds.expired and creds.refresh_token:
        logger.debug('refreshing your token!')
        creds.refresh(Request())
    if not creds:
        creds = authenticate()
    with open('token.json', 'w', encoding='utf-8') as token:
        token.write(creds.to_json())


class DriveClient:
    service = None

    def __init__(self):
        try:
            self.service = build('drive', 'v3', credentials=get_creds_from_token_file())
        except Exception as e:
            logger.error(f"The Google Drive API couldn't authenticate you. Here's the error it returned: \n{e}")
            exit(1)

    def delete_folder(self, folder_id: str):
        self.service.files().delete(fileId=folder_id, supportsAllDrives=True).execute()

    def create_folder(self, name: str, parent: str):
        file_md = {
            'name': name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent]
        }
        file = self.service.files().create(body=file_md,
                                           fields='id',
                                           supportsAllDrives=True).execute()
        return file.get('id')

    def get_file(self, file_id: str):
        return self.service.files().get(fileId=file_id,
                                        supportsAllDrives=True).execute()

    def list_files(self, folder_id: str, folders_only: bool = False, next_page_token: str = None):
        """list all items within a folder"""
        q = f"parents = '{folder_id}'"
        if folders_only:
            q += " and mimeType = 'application/vnd.google-apps.folder'"
        q += " and trashed=false"

        return self.service.files().list(q=q,
                                         fields='nextPageToken, files(id, name, mimeType)',
                                         corpora='allDrives',  # https://stackoverflow.com/a/66357508
                                         supportsAllDrives=True,
                                         includeItemsFromAllDrives=True,
                                         pageToken=next_page_token).execute()

    def get_structure(self, folder_id: str):
        """If this is an already-recreated folder structure, get its folder map"""
        q = f"parents = '{folder_id}' and mimeType = 'application/vnd.google-apps.folder'"
        folders_list = self.service.files().list(q=q,
                                                 fields='files(id, name)',
                                                 supportsAllDrives=True,
                                                 includeItemsFromAllDrives=True).execute()
        return {folder.get('name'): folder.get('id') for folder in folders_list.get('files')}

    def move_all_content_location(self, old_folder: str, new_folder: str, folders_only=False):
        """
        "Moves" all files in one folder to another folder.

        :param old_folder: folder to move from
        :param new_folder: folder to move to
        :param folders_only: only move folders (i.e. folder-type files)
        """

        q = f"parents = '{old_folder}'"
        if folders_only:
            q += f' and {Q_FOLDERSONLY}'
        response = self.list_files(folder_id=old_folder, folders_only=folders_only)
        while response:
            file_list = [listing.get('id') for listing in response.get('files')]
            next_page_token = response.get('nextPageToken', None)
            self.move_files_location(old_folder=old_folder, new_folder=new_folder, file_list=file_list)
            if next_page_token:
                response = self.list_files(folder_id=old_folder, next_page_token=next_page_token)
            else:
                response = None

    def copy_file(self, file_id: str):
        r = self.service.files().copy(fileId=file_id, fields='id', supportsAllDrives=True).execute()
        return r.get('id')

    def _move_file_location(self, old_folder: str, new_folder: str, file_id: str, copy_on_error: bool = True):
        try:
            self.service.files().update(supportsAllDrives=True, fileId=file_id, addParents=new_folder,
                                        removeParents=old_folder, fields='id, parents'
                                        ).execute()
        # should probably also catch these read timeouts somehow, though I'm not sure what to do about it. wait 1000?
        except HttpError as httpe:
            err_reason = httpe.error_details[0].get('reason')
            if err_reason == 'cannotMoveTrashedItemIntoTeamDrive':
                pass
            elif err_reason == 'fileWriterTeamDriveMoveInDisabled':
                logger.info(f'User does not own %s; skipping.', file_id)
            elif err_reason in ['fileOwnerNotMemberOfTeamDrive', 'fileOwnerNotMemberOfWriterDomain']:
                if copy_on_error:
                    # when moving files to a shared drive, if original file owner isn't a member of it
                    copy_id = self.copy_file(file_id)
                    self._move_file_location(old_folder, new_folder, copy_id)
                    logger.info(f'Owner of {file_id} is not a member of the destination drive; '
                                f'moved a copy to {new_folder} instead')
                else:
                    logger.info(f'Owner of {file_id} is not a member of the destination drive; skipping.')
            else:
                logger.critical(f'ERROR {httpe.status_code} while processing {old_folder}/{file_id} with reason '
                                f'{httpe.reason}. Details: {httpe.error_details}')
                raise httpe
        except Exception as e:
            with open('err.txt', 'a') as errfile:
                errfile.write(f'Error while processing {old_folder}/{file_id}: {str(e)}!\n')
                traceback.print_exc(file=errfile)
            raise e

    def move_files_location(self, old_folder: str, new_folder: str, file_list: list):
        """
        Move specific list of files to a different folder

        :param old_folder: folder to move from. Yes, Google needs this. Google calls "removeParents" with it.
        :param new_folder: folder to move to
        :param file_list: list of files to move
        :return:
        """
        for file_id in file_list:
            self._move_file_location(old_folder=old_folder, new_folder=new_folder, file_id=file_id)

    def change_owner(self, file_id: str, permission_id: str):
        self.service.permissions().update(supportsAllDrive=True, fileId=file_id, permissionId=permission_id,
                                          transferOwnership=True, )
