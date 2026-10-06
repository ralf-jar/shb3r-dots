function ytdv
	argparse 'r/resolution=' -- $argv; or return

	set -l format "bv*+ba/b"
	if set -q _flag_resolution
		set format "bv*[height<=$_flag_resolution]+ba/b[height<=$_flag_resolution]"
	end

	yt-dlp -f $format --merge-output-format mp4 \
		--embed-thumbnail --add-metadata \
		-o "%(title)s.%(ext)s" \
		$argv[1]
end
