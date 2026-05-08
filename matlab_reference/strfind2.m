function k = strfind2(str,pat,varargin)

numOfVar = length(varargin);
switch numOfVar
    case 1
        numOfFind = varargin{1};
        idx = strfind(str,pat);
        k = idx(1:numOfFind);
    case {2,4}
        isCheckCell = contains2(varargin,'ForceCellOutput');
        numOfFind = varargin{1};
        direction = varargin{2};
        idx = strfind(str,pat);
        k_ = idx(1:numOfFind);
        switch lower(direction)
            case {'left','l','first','1st'}
                k = k_;
            case {'right','r','last','end'}
                k = flip(k_);
        end
        if isCheckCell
            if varargin{numOfVar}
                k = num2cell(k);
            end
        end
    otherwise
        k = strfind(str,pat);
end

end