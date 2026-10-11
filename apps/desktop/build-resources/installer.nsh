; Installer hooks (M0.6). The app's own exe does the work with no window and reports problems itself, in English
; and Arabic, with the log location (src/runtime/cli.cjs, src/main.cjs). Exit codes: 0 ok, 1 failed, 2 rolled back.

!macro customInstall
  ; A main PC moves to the new runtime: backup, migrate, switch, health check; any failure rolls back.
  ; Office PCs and PCs that have not chosen a role yet: nothing to do.
  ExecWait '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --runtime-upgrade' $0
  DetailPrint "Local server update: exit code $0"
!macroend

!macro customUnInstall
  ; Not during an update (the new installer runs the old uninstaller first): the services keep running.
  ; Removes the services, the firewall rule and the runtime copies; the database, backups and files stay, so
  ; reinstalling continues with the same data.
  ${ifNot} ${isUpdated}
    ExecWait '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --runtime-uninstall' $0
    DetailPrint "Local server removal: exit code $0"
  ${endIf}
!macroend
