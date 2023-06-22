import argparse
import logging

import drive_service

logger = logging.getLogger("move")

def parse():
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", type=str, help="Source folder ID")
    parser.add_argument("--dst", type=str, help="Destination folder ID")
    parser.add_argument("--copy-on-permission-error", action="store_true", help="Copy files that can't be moved due to permission errors")
    return parser.parse_args()

def all_files(client: drive_service.DriveClient, folder_id: str, folders_only: bool = False):
    response = client.list_files(folder_id=folder_id, folders_only=folders_only)
    while response:
        for listing in response.get('files'):
            mime_type = listing.get('mimeType')
            is_folder = mime_type == 'application/vnd.google-apps.folder'
            yield listing.get('name'), listing.get('id'), is_folder
        next_page_token = response.get('nextPageToken', None)
        if next_page_token:
            response = client.list_files(folder_id=folder_id, next_page_token=next_page_token, folders_only=folders_only)
        else:
            response = None


def find_or_create_folder(client: drive_service.DriveClient, file_name: str, dst: str) -> str:
    for folder_name, folder_id, _ in all_files(client, dst, folders_only=True):
        if folder_name == file_name:
            return folder_id
    return client.create_folder(file_name, dst)


def move(client: drive_service.DriveClient, src: str, dst: str, copy_on_error: bool = False) -> None:
    logger.info('Moving from %s to %s', src, dst)
    for file_name, file_id, is_folder in all_files(client, src):
        if is_folder:
            new_src = file_id
            new_dst = find_or_create_folder(client, file_name, dst)
            logger.info('Recursing into %s', file_name)
            move(client, new_src, new_dst, copy_on_error=copy_on_error)
        else:
            logger.info('Moving %s', file_name)
            client._move_file_location(src, dst, file_id, copy_on_error=copy_on_error)

def entrypoint(args: argparse.Namespace) -> None:
    logger.info("Authenticating")
    drive_service.get_creds()
    logger.info("Starting DriveClient")
    client = drive_service.DriveClient()
    move(client, args.src, args.dst, args.copy_on_permission_error)

def main():
    logging.basicConfig(level=logging.DEBUG)
    args = parse()
    entrypoint(args)

if __name__ == "__main__":
    main()