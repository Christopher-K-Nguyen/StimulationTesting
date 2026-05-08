function isQuit = setMonitorChannel(File,channelNum)
%% Constants
% Buttons
BUTTON_QUIT = 'Quit';
BUTTON_TRY = 'Start Over';  % start over button
TITLE_ERROR = 'ERROR';
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
plexonChannel_arr = File.Parameters.Channels.Plexon;
plexonChannel = plexonChannel_arr(channelNum);
isQuit = false;

%% Function
% fprintf('Setting monitor channel for all stimulators...\n');
errSetMonitorChannel = 1;      % initialize monitor channel error
while errSetMonitorChannel ~= 0% get errors
    fprintf('Setting monitor channel...');  % monitor channel
    startTime = tic;
    
    monitorChannel_check = [];
    while ~isequal(monitorChannel_check,plexonChannel)
        errSetMonitorChannel = PS_SetMonitorChannel(1,plexonChannel);% setting monitor channel
        [monitorChannel_check,~] = PS_GetMonitorChannel(1);
        if errSetMonitorChannel ~= 0
            break;
        end
    end
    switch errSetMonitorChannel                                     % getting errors
        case 0                                                      % no errors
            [endTime,unit] = getEndTime(startTime);
            fprintf('Channel %d \t\t(%.2f %s)\n',plexonChannel,endTime,unit);  % monitor channel set
        case 1                                    % monitor channel error
            msg = 'ERROR SETTING MONITOR CHANNEL';% monitor channel not set
            fprintf('%s\n',msg);
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errSetMonitorChannel ~= 0
        switch quest                        % apply choice
            case BUTTON_QUIT            	% quit
                fprintf('\nQuitting...\n\n');	% quitting
                isQuit = true;
                return;                     % exit program
            case BUTTON_TRY               	% try again
                fprintf('\nTrying again...\n\n');% trying again
%                 try
%                     pause(5);
%                     PS_InitAllStim();
%                 catch
%                 end
            otherwise                       % cancel
                fprintf('\nQuitting...\n\n');	% quitting
                isQuit = true;
                return;                     % exit program
        end
    end
end

end