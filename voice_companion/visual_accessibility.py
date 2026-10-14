"""Keep link and focus colors readable in light and dark system themes."""
def contrast(first,second):
    def luminance(rgb):
        channels=[value/65535 for value in rgb]
        linear=[value/12.92 if value<=.04045 else ((value+.055)/1.055)**2.4 for value in channels]
        return sum(a*b for a,b in zip(linear,(.2126,.7152,.0722)))
    bright,dark=sorted((luminance(first),luminance(second)),reverse=True)
    return (bright+.05)/(dark+.05)

def readable_color(widget):
    background=widget.winfo_rgb(widget.cget('background'))
    for candidate in ('SystemHotLight',widget.cget('foreground')):
        try:
            if contrast(widget.winfo_rgb(candidate),background)>=4.5:return candidate
        except Exception:pass
    return max(('#000000','#ffffff'),key=lambda color:contrast(widget.winfo_rgb(color),background))

def focus_style(widget):
    widget.configure(highlightthickness=2,highlightcolor=readable_color(widget),highlightbackground=widget.cget('background'))
