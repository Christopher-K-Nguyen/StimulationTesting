function quitProgram = setMultipolarStim2(File,channelStim,channelReturn,pattern)
%% Variables
plexonChannel_arr = File.Parameters.Channels.Plexon;
plexonChannelStim = plexonChannel_arr(channelStim);
plexonChannelReturn = plexonChannel_arr(channelReturn);
zeroChannel_arr = 1:16;
numOfReturn = length(plexonChannelReturn);
if numOfReturn > 1
    channelReturn_char = sprintf('%d, ',channelReturn);
    len = length(channelReturn_char);
    channelReturn_char(len-1:len) = [];
else
    channelReturn_char = num2str(channelReturn);
end
zeroPattern = pattern;
zeroPattern.A1 = 0;
zeroPattern.A2 = 0;
type = '';
quitProgram = false;

%% Stimulation Type
external = File.Parameters.External;
hasExternal = ~isempty(external);
fprintf('Setting stimulation type...');
if plexonChannelReturn == 0
    zeroChannel_arr = [];
    type = 'Common Ground';
else
    zeroChannel_arr(plexonChannelReturn) = [];
    numOfReturn = length(plexonChannelReturn);
    switch numOfReturn
        case 1
            type = 'Bipolar';
        case 2
            type = 'Tripolar';
        otherwise
            numOfPoles = numOfReturn + 1;
            type = sprintf('%d-polar',numOfPoles);
    end
end
if hasExternal
    fprintf('%s\n',type);
else
    fprintf('Pseudo-%s\n',type);
end

%% Stimulation Pattern
fprintf('Setting pattern to...');
zeroChannel_arr(plexonChannelStim) = [];
PS_SetRectParam2(stimNum,plexonChannelStim,pattern);
fprintf('Channel %d\n',plexonChannelStim);

%% Stimulation Return
fprintf('Setting return to...');
for zeroChannelNum = zeroChannel_arr
    PS_SetRectParam2(1,zeroChannelNum,zeroPattern);
end
if numOfReturn > 1
    fprintf('Channels ');
else
    fprintf('Channel ');
end
fprintf('%s\n',channelReturn_char);

end