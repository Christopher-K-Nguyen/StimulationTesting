function tf = isbetween2(arr,lower,upper,varargin)
isNan_tf = isnan(arr);
arr(isNan_tf) = [];
[r,c] = size(arr);
tag = '';
tf = zeros(r,c);
if lower > upper
    lower_ = upper;
    upper_ = lower;
    lower = lower_;
    upper = upper_;
end
if isempty(lower)
    lower = -Inf;
end
if isempty(upper)
    upper = Inf;
end
if ~isempty(varargin)
    tag_raw = varargin{1};
    try
        tag = lower(tag_raw);
    catch
        tag = tag_raw;
    end
    
else
    tag = 'closed';
end

switch tag
    case 'closed'
        tf = arr >= lower & arr <= upper;
    case 'open'
        tf = arr > lower & arr < upper;
    case {'openleft','closedright'}
        tf = arr > lower & arr <= upper;
    case {'openright','closedleft'}
        tf = arr >= lower & arr < upper;
end