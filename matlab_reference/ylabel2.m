function obj = ylabel2(str,varargin)

numOfVar = length(varargin);
if numOfVar > 0
    color = varargin{1};
else
    color = 'k';
end

obj = ylabel(str, ...
    'Color',color, ...
    'Rotation',-90, ...
    'VerticalAlignment','bottom');

end