function data = readline2(obj,varargin)

data = readline(obj,varargin);

hasNewLine = contains2(data,newline);
if hasNewLine
    data = erase(data,newline);
end

end