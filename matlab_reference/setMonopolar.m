function isQuit = setMonopolar(File,channelNum,pattern,varargin)
%% Variables
if ~isempty(varargin)
    tag = varargin{1};
else
    tag = 'rect';
end
if ~isnumeric(tag)
    if contains2(tag,{'rect','default','normal'})
        type = 0;
    elseif contains2(tag,'arb')
        type = 1;
    end
end

plexonChannel_arr = File.Parameters.Channels.Plexon;
% testChannel_arr = File.Parameters.Channels.Test;
plexonChannel = plexonChannel_arr(channelNum);
% amplitude1 = pattern.A1;
% amplitude2 = pattern.A2;
zeroChannel_arr = 1:16;
zeroChannel_arr(plexonChannel) = [];
zeroPattern = pattern;
zeroPattern.A1 = 0;
zeroPattern.A2 = 0;
isQuit = false;

%% Function
fprintf('Setting pattern to...');
startTime = tic;
PS_SetPatternType(1,plexonChannel,type);
switch type
    case 0
        PS_SetRectParam2(1,plexonChannel,pattern);% get errors
    case 1
        for idx = 1:2
            PS_SetPatternType(1,plexonChannel,type);
            filename = makeArbitrary(File,pattern);
            PS_LoadArbPattern(1,channelNum,filename);
        end
end
for zeroChannelNum = zeroChannel_arr
    PS_SetPatternType(1,zeroChannelNum,type);
    switch type
        case 0
            PS_SetRectParam2(1,zeroChannelNum,zeroPattern);
        case 1
            for idx = 1:2
                PS_SetPatternType(1,plexonChannel,type);
                filename = makeArbitrary(File,zeroPattern);
                PS_LoadArbPattern(1,zeroChannelNum,filename);
            end
    end
end
[endTime,unit] = getEndTime(startTime);
fprintf('Channel %d (%.2f %s)\n',channelNum,endTime,unit);

end