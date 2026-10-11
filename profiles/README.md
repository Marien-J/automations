# Profiles

Your calibrated setups live here, one folder per automation and profile:

```
profiles/<automation-id>/<profile-name>/
    settings.yaml       your overrides of the automation's defaults.yaml
    calibration.json    regions, colours, where each template was captured
    templates/*.png     reference images captured from your client
    runs/<timestamp>/   events.jsonl and screenshots of each run
    STOP                create this file to stop a running automation
```

Everything except this README is git-ignored: profiles contain screenshots of
your own client.
