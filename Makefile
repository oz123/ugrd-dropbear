install:
	UGRD_PATH=$$(python3 -c 'import ugrd; print(ugrd.__path__[0])') && \
	sudo -E cp -v dropbear.py $$UGRD_PATH/dropbear.py && \
	sudo -E cp -v dropbear.toml $$UGRD_PATH/dropbear.toml

regenerate-initrmfs:
	sudo rm -iv /boot/initramfs-$$(uname -r).img
	sudo ugrd /boot/initramfs-$$(uname -r).img
