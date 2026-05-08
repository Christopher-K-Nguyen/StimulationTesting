function varargout = getNeighbor(channelMapping,channelStim,varargin)
%% Variables
numOfPositions = numel(channelMapping);
channelReturn_arr = [];
distance_arr = [];
if ~isempty(varargin)
    numOfVar = length(varargin);
    switch numOfVar
        case 1
            tag_raw = varargin{1};
            if contains2(tag_raw,'side')
                tag = 'side';
            elseif contains2(tag_raw,'diag')
                tag = 'diag';
            elseif contains2(tag_raw,'adj')
                tag = 'adj';
            elseif contains2(tag_raw,{'any','all'})
                tag = 'all';
            end
            distance = 1;
        case 2
            tag = varargin{1};
            distance = varargin{2};
    end
else
    distance = 1;
    tag = 'adj';
end

distance_diag = distance * sqrt(2);
switch tag
    case 'side'
        distance_use = distance;
    case 'diag'
        distance_use = distance_diag;
    case 'adj'
        distance_use = [distance distance_diag];
    case {'any','all'}
        distance_use = 0;
end

%% Function
fprintf('Getting Channel %d neighbors....',channelStim);
startTime = tic;
[stimRow,stimCol] = find(channelMapping == channelStim);
stimXY = [stimRow,stimCol];
for positon_idx = 1:numOfPositions
    channelNum = channelMapping(positon_idx);
    if channelNum == 0 || channelNum == channelStim
        continue;
    end
    [checkRow,checkCol] = find(channelMapping == channelNum);
    checkXY = [checkRow,checkCol];
    distance_check = norm(stimXY - checkXY);
    if contains2(tag,'all')
        channelReturn_alloc = [channelReturn_arr,channelNum];
        channelReturn_arr = sort(channelReturn_alloc);
        distance_arr_alloc = [distance_arr,distance_check];
        distance_arr = distance_arr_alloc;
    else
        if ismember(distance_check,distance_use)
            channelReturn_alloc = [channelReturn_arr,channelNum];
            channelReturn_arr = sort(channelReturn_alloc);
            distance_arr_alloc = [distance_arr,distance_check];
            distance_arr = distance_arr_alloc;
        end
    end
end

varargout{1} = channelReturn_arr;
varargout{2} = distance_arr;

channelReturn_char = sprintf('%d, ',channelReturn_arr);
len = length(channelReturn_char);
channelReturn_char(len-1:len) = [];
[endTime,unit] = getEndTime(startTime);
fprintf('%s (%.2f %s)\n',channelReturn_char,endTime,unit);

end