# C1 file downloads

This container will download files from an sftp and copy them to a file
server where they can be accessed.

Future plans to add a web interface to fetch files view log, manualy run job.

## Manually start job

``` bash
docker exec -it ${CONTAINER_NAME} /app/c1get.sh
```

## View Log

``` bash
docker exec -it ${CONTAINER_NAME} cat /var/log/sftp_sync_latest.log
```
