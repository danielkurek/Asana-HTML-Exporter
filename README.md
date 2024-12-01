# Asana HTML exporter

A tool to export Asana workspaces to json and basic HTML.

## Usage

1. Get Asana [Personal access token (PAT)](https://developers.asana.com/docs/personal-access-token)
2. Create `.env` file with your Asana PAT
3. (Optional) create virtual python environment
   ```
   python3 -m venv .venv
   ```
   - to activate it run `source .venv/bin/activate` on Linux, and `.\venv\Scripts\activate` on Windows
4. Install required python libraries
   ```bash
   python3 -m pip install -r requirements.txt
   ```
5. Run `exporter.py` (you can use parameters to customize the behavior - see section [advanced usage](#advanced-usage))
```bash
python exporter.py
```

### Advanced usage

Append parameters to the base command. There must be a space between each parameter. Examples are shown below.

Parameters:

- `-d` or `--download-attachments`
  - values: `0` or `1`
  - default value: `1`
  - specifies if attachments should be downloaded during exporting process (cannot be used with `--load-local-responses`)
- `-r` or `--save-raw-responses`
  - values: `0` or `1`
  - default value: `1`
  - specifies if raw json responses should be saved
  - **highly recommended to keep this enabled** - can be used to regenerate HTML files without downloading everything from Asana again
- `-e` or `--export-html`
  - values: `0` or `1`
  - default value: `1`
  - specifies if HTML files should be generated during export
- `-o` or `--output-dir`
  - value: path to output directory
  - default: `out/`
  - specifies directory for the export output (creates the directory if it does not exists)
- `-s` or `--separate-responses`
  - split json and html files into separate directories if present - `<output_dir>/json/` and `<output_dir>/html/`
  - downloaded attachments will be stored in `html` directory
  - need to specified also for `--load-local-responses` if it was used during exporting from Asana API
- `--load-local-responses`
  - load API responses from json files from output directory instead of using Asana API
  - main usage: regenerate HTML files after updating HTML templates
- `-l` or `--locale`
  - value: python locale value
  - default locale: according to system settings
  - specify your locale
  - used for locale aware sorting in HTML output (not needed if you do not care about correct sorting)
- `--log-file`
  - value: name of log file
  - used for debugging

#### Examples

- regenerate HTML files (without separated responses)
  ```shell
  python exporter.py --load-local-responses
  ```
- regenerate HTML files (with separated responses)
  ```shell
  python exporter.py -s --load-local-responses
  ```
- specify Czech locale - for correct sorting in HTML outputs
  ```shell
  python exporter.py -l 'cs_CZ.UTF-8'
  ```
- do not download attachments
  ```shell
  python exporter.py -d 0
  ```
- change output directory to `alternative_directory/` and separate responses
  ```shell
  python exporter.py -s --output-dir alternative_directory/
  ```

## License

My work is licensed under MIT License. Libraries used for this project have their own licenses.