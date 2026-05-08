function tline = fgetl2(fid,varargin)

numOfVar = length(varargin);
if numOfVar > 0
    query = varargin{1};
    if ischar(query)
        fprintf(fid,query);
    end
end

tline = fgetl(fid);
hasNewLine = contains2(tline,newline);
if hasNewLine
    tline = erase(tline,newline);
end

end
