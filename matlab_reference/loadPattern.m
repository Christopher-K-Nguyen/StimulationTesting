function isQuit = loadPattern(File,varargin)
%% Constants
% Buttons
BUTTON_QUIT = 'Quit';
BUTTON_TRY = 'Start Over';  % start over button
TITLE_ERROR = 'ERROR';
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
isQuit = false;
plexonChannel_arr = File.Parameters.Channels.Plexon;
numOfChannels = length(plexonChannel_arr);
channelLoad_arr = plexonChannel_arr;
% configID = File.Parameters.Configuration.ID;
if ~isempty(varargin)
    channelReturn_arr = varargin{1};
else
    channelReturn_arr = [];
end
numOfReturns = length(channelReturn_arr);
plexonReturn_arr = plexonChannel_arr(channelReturn_arr);
channelLoad_arr(plexonReturn_arr) = [];
numOfLoad = length(channelLoad_arr);

%% Function
fprintf('Loading channels to stimulator...');
startTime = tic;
errLoadChannel = 1;                                 % initialize errors
while errLoadChannel ~= 0                           % load channel stimulation
    if numOfLoad == numOfChannels || isempty(channelReturn_arr)
        fprintf('all...');
       errLoadChannel = PS_LoadAllChannels(1);   % get errors
       % fprintf('OK');
    elseif numOfLoad == 1
        fprintf('%d...',channelLoad_arr);
        errLoadChannel = PS_LoadChannel(1,channelLoad_arr);
    else
        for load_idx = 1:numOfLoad
            channelLoad = channelLoad_arr(load_idx);
            fprintf('%d...',channelLoad);
            errLoadChannel = PS_LoadChannel(1,channelLoad);
            if errLoadChannel ~= 0
                break;
            end
            % if load_idx < numOfLoad
               
            % else
            %     fprintf('%d',channelLoad);
            % end
        end
    end
    fprintf('OK');
    switch errLoadChannel                               % getting errors
        case 0                                          % no errors
            [endTime,unit] = getEndTime(startTime);
            fprintf(' \t\t(%.2f %s)\n',endTime,unit);     % loaded channel stimulation
            if numOfReturns > 0
                if numOfReturns > 1
                    channelReturn_cell = char2(channelReturn_arr);
                    channelReturn_char = strjoin(channelReturn_cell,', ');
                else
                    channelReturn_char = num2str(channelReturn_arr);
                end
                fprintf('\tNot loaded: Channel %s\n',channelReturn_char);
            end
        case 1                                                          % errors found
            msg = 'ERROR LOADING PARAMETERS';                           % errors loading parameters
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
        case 3                                                          % errors found
            msg = 'CRC ERROR';                                          % CRC error
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
        case 6                                                          % errors found
            msg = 'STIMULATION PATTERN NOT READY';                      % pattern not ready
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
        case -1                                                         % errors found
            msg = 'INVALID ARGUMENT(S)';                                % invalide argument(s)
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errLoadChannel ~= 0
        switch quest                        % apply choice
            case BUTTON_QUIT            	% quit
                fprintf('Quitting...\n\n');	% quitting
                isQuit = true;
                break;                     % exit program
            case BUTTON_TRY               	% try again
                fprintf('Trying again...\n\n');% trying again
            otherwise                       % cancel
                fprintf('Quitting...\n\n');	% quitting
                isQuit = true;
                break;                     % exit program
        end
    end
end
if isQuit
    return;
end

end