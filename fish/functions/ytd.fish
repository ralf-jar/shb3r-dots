function ytd
	yt-dlp -x --audio-format mp3 --audio-quality 0 \
		--embed-thumbnail --add-metadata \
		-o "%(title)s.%(ext)s" \
		$argv[1]
end
