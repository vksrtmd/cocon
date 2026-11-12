# Detached sessions run in host tmux, not tmux inside the container

`cocon --detach` runs the launcher itself inside a tmux session on the Mac. The container keeps its
`run --rm` lifetime, and the launcher stays in the foreground of that tmux pane, so its post-session git-config
and hooks check still runs when the session ends, even if nobody ever reattaches. tmux in the image (or
`compose up -d` with `docker attach`) would need a long-lived named container and a check moved to attach or
stop time, which a never-reattached session skips. The cost is tmux on the host (`brew install tmux`).
