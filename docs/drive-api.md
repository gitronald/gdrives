# Google Drive API v3 — File Fields Reference

Fields available on `files.list` and `files.get` responses. Request specific fields via the `fields` parameter (e.g., `files(id, name, modifiedTime)`). Using `files(*)` returns all fields but is slower.

## Core

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Unique file ID |
| `name` | `str` | Display name (not unique — Drive allows duplicates) |
| `mimeType` | `str` | MIME type (e.g., `application/pdf`, `application/vnd.google-apps.document`) |
| `kind` | `str` | Always `drive#file` |

## Timestamps

| Field | Type | Description |
|---|---|---|
| `createdTime` | `str` (ISO 8601) | When the file was created |
| `modifiedTime` | `str` (ISO 8601) | Last modified by anyone |
| `modifiedByMeTime` | `str` (ISO 8601) | Last modified by the authenticated user |
| `viewedByMeTime` | `str` (ISO 8601) | Last viewed by the authenticated user |
| `sharedWithMeTime` | `str` (ISO 8601) | When the file was shared with the authenticated user |

## People

| Field | Type | Description |
|---|---|---|
| `owners` | `list[dict]` | List of owners. Each has `displayName`, `emailAddress`, `permissionId`, `photoLink`, `me` (bool). Typically one entry. |
| `lastModifyingUser` | `dict` | Who last modified the file. Same fields as an owner entry. |
| `sharingUser` | `dict` | Who shared the file with the authenticated user. Same fields as an owner entry. Only present on `sharedWithMe` items. |
| `ownedByMe` | `bool` | Whether the authenticated user owns the file |
| `modifiedByMe` | `bool` | Whether the authenticated user has ever modified the file |

## Size and content

| Field | Type | Description |
|---|---|---|
| `size` | `str` (numeric) | File size in bytes. Not available for Google-native files (Docs, Sheets, etc.). |
| `quotaBytesUsed` | `str` (numeric) | Storage quota consumed |
| `md5Checksum` | `str` | MD5 hash of file content (binary files only) |
| `sha1Checksum` | `str` | SHA-1 hash |
| `sha256Checksum` | `str` | SHA-256 hash |
| `fullFileExtension` | `str` | Full file extension (e.g., `tar.gz`). Not present for Google-native files. |
| `fileExtension` | `str` | Final extension only (e.g., `gz`) |
| `originalFilename` | `str` | Original filename at upload time |
| `headRevisionId` | `str` | ID of the latest revision |

## Hierarchy

| Field | Type | Description |
|---|---|---|
| `parents` | `list[str]` | List of parent folder IDs. Usually one entry. Legacy files may have multiple. |
| `spaces` | `list[str]` | Which spaces the file is in (e.g., `["drive"]`, `["appDataFolder"]`) |
| `driveId` | `str` | ID of the shared drive containing the file (absent for My Drive files) |

## URLs

| Field | Type | Description |
|---|---|---|
| `webViewLink` | `str` | URL to view the file in a browser |
| `webContentLink` | `str` | URL to download the file directly (binary files only) |
| `iconLink` | `str` | URL to the file type icon |
| `thumbnailLink` | `str` | URL to a thumbnail image |
| `hasThumbnail` | `bool` | Whether a thumbnail is available |
| `thumbnailVersion` | `str` | Thumbnail version number |

## Permissions

| Field | Type | Description |
|---|---|---|
| `permissions` | `list[dict]` | Full permissions list. Each entry has `id`, `type` (`user`, `group`, `domain`, `anyone`), `role` (`owner`, `organizer`, `fileOrganizer`, `writer`, `commenter`, `reader`), `emailAddress`, `displayName`, `deleted`, `pendingOwner`. |
| `permissionIds` | `list[str]` | List of permission IDs (shorter than full `permissions`) |
| `shared` | `bool` | Whether the file is shared with anyone |
| `writersCanShare` | `bool` | Whether editors can share the file |
| `copyRequiresWriterPermission` | `bool` | Whether viewers/commenters are blocked from copying |
| `viewersCanCopyContent` | `bool` | Whether viewers can copy content |

## Capabilities

| Field | Type | Description |
|---|---|---|
| `capabilities` | `dict` | What the authenticated user can do with this file. Keys include `canEdit`, `canDelete`, `canRename`, `canShare`, `canCopy`, `canDownload`, `canTrash`, `canMoveItemWithinDrive`, `canMoveItemOutOfDrive`, `canAddChildren`, `canListChildren`, `canModifyContent`, etc. All values are `bool`. |

## Status

| Field | Type | Description |
|---|---|---|
| `starred` | `bool` | Whether the user has starred the file |
| `trashed` | `bool` | Whether the file is in the trash |
| `explicitlyTrashed` | `bool` | Whether the file was trashed directly (vs. a parent being trashed) |
| `version` | `str` (numeric) | Monotonically increasing version number |

## Shortcuts

| Field | Type | Description |
|---|---|---|
| `shortcutDetails` | `dict` | Present only for shortcut files. Has `targetId` (the file ID it points to) and `targetMimeType`. |

## Sharing and security

| Field | Type | Description |
|---|---|---|
| `linkShareMetadata` | `dict` | Has `securityUpdateEligible` and `securityUpdateEnabled` (both `bool`). Related to the 2021 Drive security update for link sharing. |
| `isAppAuthorized` | `bool` | Whether the file was created/opened by the requesting app |
| `inheritedPermissionsDisabled` | `bool` | Whether inherited permissions are disabled |

## Download restrictions

| Field | Type | Description |
|---|---|---|
| `downloadRestrictions` | `dict` | Nested structure with `itemDownloadRestriction` and `effectiveDownloadRestrictionWithContext`, each containing `restrictedForReaders` and `restrictedForWriters` (both `bool`). |

## Notes

- Google-native files (Docs, Sheets, Slides, etc.) have no `size`, `md5Checksum`, `fileExtension`, or `webContentLink`
- Timestamps are UTC in ISO 8601 format (e.g., `2026-02-16T19:46:59.000Z`)
- `size` and `quotaBytesUsed` are strings, not integers
- `parents` is usually a single-element list, but legacy files may have multiple parents
- `capabilities` keys vary by file type and user role
