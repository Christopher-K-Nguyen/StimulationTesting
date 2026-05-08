function rgb = hex2rgb(hex)

hex = strrep(hex,'#','');
r = hex2dec(hex(1:2));
g = hex2dec(hex(3:4));
b = hex2dec(hex(5:6));
rgb = [r g b] / 255;

end